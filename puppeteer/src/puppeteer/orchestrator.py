"""Main orchestrator for game lifecycle management."""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from openai import OpenAIError

from puppeteer.config import Config, PilotPlayer, Player
from puppeteer.deck_choice import resolve_choice_decks
from puppeteer.game_log import merge_game_log, read_decklist
from puppeteer.harness_epoch import HARNESS_EPOCH
from puppeteer.llm_cost import DEFAULT_LLM_PROVIDER, required_api_key_env
from puppeteer.log import get_logger, setup_logging
from puppeteer.port import find_available_port, wait_for_port
from puppeteer.process_manager import ProcessManager, jvm_oom_preexec_fn, kill_tree
from puppeteer.xml_config import modify_server_config
from scripts.analysis.blunder_analysis import (
    BlunderAnalysisError,
)
from scripts.analysis.blunder_analysis import (
    main as _analyze_blunders,
)
from scripts.export_game import GameExportError
from scripts.export_game import export_game as _export_game
from scripts.generate_leaderboard import generate_all_website_data
from scripts.upload_youtube import (
    YouTubeUploadError,
)
from scripts.upload_youtube import (
    upload_to_youtube as _upload_to_youtube,
)

logger = get_logger(__name__)

_SPECTATOR_TABLE_READY = "AI Puppeteer: waiting for"
_SPECTATOR_GAME_STARTED = "AI Puppeteer: all players joined"
_LOG_TIMESTAMP_TZ = ZoneInfo("America/Los_Angeles")


def _git(cmd: str, cwd: Path) -> str:
    """Run a git command and return stripped stdout."""
    argv = ["git", *shlex.split(cmd)]
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, subprocess.CalledProcessError):
            detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        else:
            detail = str(exc)
        raise RuntimeError(f"git command failed in {cwd}: {' '.join(argv)}: {detail}") from exc


def _wait_for_spectator_table(log_path: Path, proc: subprocess.Popen, timeout: int = 300) -> None:
    """Block until the spectator log indicates the game table is ready.

    The observer/GUI client logs a line containing ``AI Puppeteer: waiting
    for … bridge client(s)`` once it has created the table.  We poll the
    log file for that marker so bridge clients aren't started before the
    table exists.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("Spectator process exited before creating the game table")
        if log_path.exists():
            text = log_path.read_text()
            if _SPECTATOR_TABLE_READY in text:
                return
        time.sleep(2)
    raise TimeoutError(f"Spectator did not create a table within {timeout}s — check {log_path}")


def _wait_for_game_start(log_path: Path, proc: subprocess.Popen, timeout: int = 600) -> None:
    """Block until the spectator log indicates all players have joined and the game started.

    Used in parallel mode to ensure a game's table has left the WAITING state
    before starting the next game's spectator.  This prevents bridge clients
    from joining the wrong table.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return  # Process exited — game may have started and ended quickly
        if log_path.exists():
            text = log_path.read_text()
            if _SPECTATOR_GAME_STARTED in text:
                return
        time.sleep(2)
    raise TimeoutError(f"Game did not start within {timeout}s — check {log_path}")


def _missing_llm_api_keys(config: Config) -> list[str]:
    """Return validation errors for LLM players missing required API keys."""
    errors: list[str] = []
    llm_players = [*config.pilot_players]
    for player in llm_players:
        configured_provider = player.provider
        provider = configured_provider or DEFAULT_LLM_PROVIDER
        try:
            key_env = required_api_key_env(configured_provider)
        except ValueError as exc:
            errors.append(f"{player.name} ({provider}): {exc}")
            continue
        if not os.environ.get(key_env, "").strip():
            errors.append(f"{player.name} ({provider}) is missing the required API key")
    return errors


def _missing_llm_api_keys_for_run(config: Config) -> list[str]:
    """Return missing-key validation errors for a single config or batch manifest."""
    if not config.batch_config_files:
        return _missing_llm_api_keys(config)

    errors: list[str] = []
    for config_file in config.batch_config_files:
        game_config = Config(config_file=config_file)
        game_config.load_config()
        errors.extend(f"{config_file}: {missing}" for missing in _missing_llm_api_keys(game_config))
    return errors


def bring_to_foreground_macos() -> None:
    """Bring the Java app to foreground on macOS using AppleScript."""
    if sys.platform != "darwin":
        return

    time.sleep(2)  # Wait for window to appear

    subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "System Events" to set frontmost of first process whose name contains "java" to true',
        ],
        capture_output=True,
    )


def _wait_with_pilot_monitoring(
    spectator_proc: subprocess.Popen,
    pilot_procs: list[tuple[str, subprocess.Popen]],
    pm: ProcessManager,
    poll_interval: float = 2.0,
) -> int:
    """Wait for the spectator to exit, but abort if any pilot dies with an error.

    Polls the spectator and all pilot processes every *poll_interval* seconds.
    If a pilot exits with a non-zero return code (e.g. PERMANENT_FAILURE_EXIT_CODE
    for model-not-found), kills everything and returns early.

    Returns the spectator's exit code, or -1 if we killed it due to a pilot failure.
    """
    while True:
        # Check spectator first
        spectator_rc = spectator_proc.poll()
        if spectator_rc is not None:
            if spectator_rc != 0:
                # Spectator crashed — kill everything (server, bridges,
                # pilots) so they don't continue playing an unrecorded game.
                logger.error("Spectator exited with code %s — aborting game.", spectator_rc)
                pm.cleanup()
            return spectator_rc

        # Check all pilot processes
        for name, proc in pilot_procs:
            rc = proc.poll()
            if rc is not None and rc != 0:
                logger.error("Pilot '%s' exited with code %s — aborting game.", name, rc)
                pm.cleanup()
                return -1

        time.sleep(poll_interval)


def _ensure_game_over_event(game_dir: Path, spectator_exit_code: int = -1) -> None:
    """Append a game_over event to game_events.jsonl if one is missing.

    When the game ends via time limit, user closing the spectator window, or
    process kill, XMage may not fire a GAME_OVER callback. This ensures the
    event log always has a termination record for downstream analysis.

    The spectator_exit_code is used to distinguish reasons:
    - 0: spectator exited cleanly (user closed window or normal shutdown)
    - non-zero / -1: spectator crashed or was killed
    """
    events_file = game_dir / "game_events.jsonl"
    has_game_over = False
    max_seq = 0
    if events_file.exists():
        try:
            with open(events_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    seq = event.get("seq", 0)
                    if isinstance(seq, int) and seq > max_seq:
                        max_seq = seq
                    if event.get("type") == "game_over":
                        has_game_over = True
                        break
        except OSError:
            pass

    if not has_game_over:
        if spectator_exit_code == 0:
            reason = "spectator_closed"
            message = "Game interrupted (spectator window closed)"
        else:
            reason = "spectator_crashed"
            message = f"Game ended (spectator exited with code {spectator_exit_code})"
        ts = datetime.now(_LOG_TIMESTAMP_TZ).isoformat(timespec="milliseconds")
        event = {
            "ts": ts,
            "seq": max_seq + 1,
            "type": "game_over",
            "message": message,
            "reason": reason,
        }
        try:
            with open(events_file, "a") as f:
                f.write(json.dumps(event, separators=(",", ":")) + "\n")
        except OSError:
            pass


def _write_error_log(game_dir: Path) -> None:
    """Combine per-player error logs into a unified errors.log.

    Each player (pilot) writes errors to {name}_errors.log
    in real-time. This just concatenates them into one file.
    """
    error_lines: list[str] = []
    for log_file in sorted(game_dir.glob("*_errors.log")):
        try:
            error_lines.extend(
                f"[{log_file.stem}] {line}" for line in log_file.read_text().splitlines() if line.strip()
            )
        except OSError:
            pass

    error_log = game_dir / "errors.log"
    if error_lines:
        error_log.write_text("\n".join(error_lines) + "\n")
        logger.info("  Errors: %d (see %s)", len(error_lines), error_log)
    else:
        error_log.write_text("No errors detected.\n")


def _write_game_meta(game_dir: Path, config: Config, project_root: Path) -> None:
    """Write game_meta.json with player configs, decklists, format, and git info."""
    assert config.game_type, "game_meta requires non-empty config.game_type"
    assert config.deck_type, "game_meta requires non-empty config.deck_type"
    players = []
    all_players: list[tuple[Player, str]] = [
        *((p, "pilot") for p in config.pilot_players),
        *((p, "sleepwalker") for p in config.sleepwalker_players),
        *((p, "potato") for p in config.potato_players),
        *((p, "staller") for p in config.staller_players),
        *((p, "cpu") for p in config.cpu_players),
    ]
    for player, ptype in all_players:
        entry: dict[str, str | list[str]] = {"name": player.name, "type": ptype}
        if player.deck:
            entry["deck_path"] = player.deck
            deck_file = project_root / player.deck
            entry["decklist"] = read_decklist(deck_file)
        if player.deck_name:
            entry["deck_name"] = player.deck_name
        if player.deck_strategy:
            entry["deck_strategy"] = player.deck_strategy
        if isinstance(player, PilotPlayer):
            if player.model:
                entry["model"] = player.model
            if player.personality:
                entry["personality"] = player.personality
            if player.system_prompt:
                entry["system_prompt"] = player.system_prompt
            if player.reasoning_effort:
                entry["reasoning_effort"] = player.reasoning_effort
        players.append(entry)

    meta = {
        "timestamp": config.timestamp,
        "config": config.run_tag,
        "game_type": config.game_type,
        "deck_type": config.deck_type,
        "harness_epoch": HARNESS_EPOCH,
        "season": json.loads((project_root / "data" / "season.json").read_text())["current_season"],
        "players": players,
        "git_branch": _git("rev-parse --abbrev-ref HEAD", project_root),
        "git_commit": _git("rev-parse --short HEAD", project_root),
    }
    if config.tournament_game:
        meta["tournament_game"] = True
    (game_dir / "game_meta.json").write_text(json.dumps(meta, indent=2) + "\n")


def _print_game_summary(game_dir: Path) -> float:
    """Print a summary of game results and costs after the game ends.

    Returns total pilot cost in USD.
    """
    logger.info("=" * 60)
    logger.info("GAME SUMMARY")
    logger.info("=" * 60)

    # Scan bridge client logs for "Game over:" messages
    game_over_found = False
    for log_file in sorted(game_dir.glob("*_pilot.log")) + sorted(game_dir.glob("*_mcp.log")):
        try:
            text = log_file.read_text()
            for line in text.splitlines():
                if "Game over:" in line:
                    game_over_found = True
                    logger.info("  %s", line.strip())
                    break  # Only first game_over per log (client may join next game)
        except OSError:
            pass

    # Fall back to game_events.jsonl (written by the observer spectator).
    # CPU-only games have no bridge client logs, but the spectator still
    # records a game_over event.
    if not game_over_found:
        events_file = game_dir / "game_events.jsonl"
        if events_file.exists():
            try:
                for line in events_file.read_text().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "game_over":
                        reason = event.get("reason", "")
                        msg = event.get("message", "")
                        if reason == "spectator_closed":
                            game_over_found = True
                            logger.info("  %s", msg)
                        elif reason not in ("timeout_or_killed", "spectator_crashed") and msg:
                            game_over_found = True
                            logger.info("  Game over: %s", msg)
                        break
            except OSError:
                pass

    if not game_over_found:
        logger.info("  Game did not finish (killed or disconnected)")

    # Extract turn count from game events
    max_turn = 0
    events_file = game_dir / "game_events.jsonl"
    if events_file.exists():
        try:
            turn_pattern = re.compile(r"TURN (\d+) for ")
            for line in events_file.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = event.get("message", "")
                m = turn_pattern.search(msg)
                if m:
                    max_turn = max(max_turn, int(m.group(1)))
        except OSError:
            pass
    if max_turn > 0:
        logger.info("  Turns: %d", max_turn)

    # Count actions per player from LLM JSONL files and collect costs
    llm_files = sorted(game_dir.glob("*_llm.jsonl"))
    player_actions: dict[str, int] = {}
    for llm_file in llm_files:
        player = llm_file.stem.replace("_llm", "")
        actions = 0
        try:
            for line in llm_file.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "llm_response" and entry.get("tool_calls"):
                    actions += len(entry["tool_calls"])
        except OSError:
            pass
        if actions > 0:
            player_actions[player] = actions

    # Print per-player costs and actions
    cost_files = sorted(game_dir.glob("*_cost.json"))
    total_cost = 0.0
    if cost_files or player_actions:
        logger.info("")
        for cost_file in cost_files:
            try:
                data = json.loads(cost_file.read_text())
                cost = data.get("cost_usd", 0.0)
                player = cost_file.stem.replace("_cost", "")
                total_cost += cost
                actions_str = ""
                if player in player_actions:
                    actions_str = f" ({player_actions.pop(player)} actions)"
                logger.info("  %s: $%.4f%s", player, cost, actions_str)
            except (OSError, json.JSONDecodeError):
                pass
        # Print any players with actions but no cost file (shouldn't happen, but just in case)
        for player, actions in player_actions.items():
            logger.info("  %s: %d actions", player, actions)
        logger.info("  Total: $%.4f", total_cost)

    logger.info("=" * 60)
    return total_cost


def _print_run_cost_summary(
    sessions: list["GameSession"],
    pilot_costs: dict[int, float],
    blunder_costs: dict[int, float],
) -> None:
    """Print aggregate cost summary across all games."""
    logger.info("")
    logger.info("=" * 60)
    logger.info("COST SUMMARY")
    logger.info("=" * 60)

    grand_pilot = 0.0
    grand_blunder = 0.0

    for session in sessions:
        pilot = pilot_costs.get(session.index, 0.0)
        blunder = blunder_costs.get(session.index, 0.0)
        grand_pilot += pilot
        grand_blunder += blunder

        if len(sessions) > 1:
            logger.info("  %s:", session.game_dir.name)
            logger.info("    Game:     $%.4f", pilot)
            logger.info("    Blunders: $%.4f", blunder)
            logger.info("    Subtotal: $%.4f", pilot + blunder)

    if len(sessions) == 1:
        logger.info("  Game:     $%.4f", grand_pilot)
        logger.info("  Blunders: $%.4f", grand_blunder)
    else:
        logger.info("")
        logger.info("  All games:    $%.4f", grand_pilot)
        logger.info("  All blunders: $%.4f", grand_blunder)

    logger.info("  Total:        $%.4f", grand_pilot + grand_blunder)
    logger.info("=" * 60)


def parse_args() -> Config:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="XMage AI Puppeteer")
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to player config JSON",
    )
    parser.add_argument(
        "--batch-config-manifest",
        type=Path,
        help="Path to a JSON array of per-game config files",
    )
    parser.add_argument(
        "--observer",
        action="store_true",
        help="Launch the observer spectator client (auto-requests hand permissions)",
    )
    parser.add_argument(
        "--record",
        nargs="?",
        const=True,
        default=False,
        metavar="PATH",
        help="Record game to video file (optionally specify output path)",
    )
    parser.add_argument(
        "--games",
        type=int,
        default=1,
        help="Number of parallel games on the same server (default: 1)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG-level logging (verbose MCP details, process management)",
    )
    parser.add_argument(
        "--skip-compile",
        action="store_true",
        help="Skip compilation (caller already compiled)",
    )
    args = parser.parse_args()
    assert not (args.config and args.batch_config_manifest), (
        "--config and --batch-config-manifest are mutually exclusive"
    )

    # Determine record output path
    record_output = None
    if args.record and args.record is not True:
        record_output = Path(args.record)

    batch_config_files: list[Path] = []
    config_file = args.config
    num_games = args.games
    if args.batch_config_manifest:
        manifest = json.loads(args.batch_config_manifest.read_text())
        assert isinstance(manifest, list) and manifest, (
            f"Batch config manifest must be a non-empty JSON array: {args.batch_config_manifest}"
        )
        for i, item in enumerate(manifest):
            assert isinstance(item, str) and item, f"Batch config manifest entry {i} must be a non-empty string path"
            path = Path(item)
            assert path.exists(), f"Batch config file not found: {path}"
            batch_config_files.append(path)
        config_file = batch_config_files[0]
        if args.games != 1:
            assert args.games == len(batch_config_files), (
                f"--games ({args.games}) must match batch config count ({len(batch_config_files)})"
            )
        num_games = len(batch_config_files)

    return Config(
        config_file=config_file,
        batch_config_files=batch_config_files,
        observer=args.observer,
        record=bool(args.record),
        record_output=record_output,
        num_games=num_games,
        debug=args.debug,
        skip_compile=args.skip_compile,
    )


def compile_project(
    project_root: Path,
    observer: bool = False,
    populate_local_repo: bool = False,
) -> bool:
    """Compile the project using Maven."""
    logger.info("Compiling project...")
    modules = "Mage.Server,Mage.Client,Mage.Client.Bridge"
    if observer:
        modules += ",Mage.Client.Observer"

    cmd = [
        "mvn",
        "-q",
        "-DskipTests",
        "-pl",
        modules,
        "-am",
    ]
    if populate_local_repo:
        # Golden tests compute per-module classpaths with separate Maven invocations.
        # Disable the build cache here so install writes reactor artifacts into the
        # local Maven repo instead of restoring only target/classes from cache.
        cmd.append("-Dmaven.build.cache.enabled=false")
    cmd.append("install")

    result = subprocess.run(cmd, cwd=project_root, preexec_fn=jvm_oom_preexec_fn())
    return result.returncode == 0


def refresh_observer_resources(project_root: Path) -> bool:
    """Refresh observer client resources under target/classes."""
    result = subprocess.run(
        [
            "mvn",
            "-q",
            "-pl",
            "Mage.Client.Observer",
            "resources:resources",
        ],
        cwd=project_root,
        preexec_fn=jvm_oom_preexec_fn(),
    )
    return result.returncode == 0


def clean_stale_h2_locks(project_root: Path) -> None:
    """Remove stale H2 lock files left by previously killed server processes."""
    db_dir = project_root / "Mage.Server" / "db"
    for lock_file in db_dir.glob("*.lock.db"):
        logger.info("Removing stale DB lock file: %s", lock_file)
        lock_file.unlink()


def start_server(
    pm: ProcessManager,
    project_root: Path,
    config: Config,
    config_path: Path,
    log_path: Path,
) -> subprocess.Popen:
    """Start the XMage server."""
    jvm_args = " ".join(
        [
            config.jvm_bridge_opts,
            "-Xmx1024m",
            f"-Dxmage.config.path={config_path}",
        ]
    )

    env = {
        "XMAGE_AI_PUPPETEER": "1",
        "XMAGE_AI_PUPPETEER_USER": config.user,
        "XMAGE_AI_PUPPETEER_PASSWORD": config.password,
        "XMAGE_AI_PUPPETEER_SERVER": config.server,
        "XMAGE_AI_PUPPETEER_PORT": str(config.port),
        "XMAGE_AI_PUPPETEER_DISABLE_WHATS_NEW": "1",
        "MAVEN_OPTS": jvm_args,
    }

    return pm.start_jvm_process(
        args=["mvn", "-q", "exec:java"],
        cwd=project_root / "Mage.Server",
        env=env,
        log_file=log_path,
    )


def start_gui_client(
    pm: ProcessManager,
    project_root: Path,
    config: Config,
    log_path: Path,
    game_dir: Path | None = None,  # Unused; matches start_observer_client signature
) -> subprocess.Popen:
    """Start the GUI client."""
    # Pass resolved player config (with actual deck paths, not "random")
    config_json = config.get_players_config_json()

    jvm_args = " ".join(
        [
            config.jvm_opens,
            config.jvm_rendering,
            "-Xmx1536m",
            "-Dxmage.aiPuppeteer.autoConnect=true",
            "-Dxmage.aiPuppeteer.autoStart=true",
            "-Dxmage.aiPuppeteer.disableWhatsNew=true",
            f"-Dxmage.aiPuppeteer.server={config.server}",
            f"-Dxmage.aiPuppeteer.port={config.port}",
            f"-Dxmage.aiPuppeteer.user={config.user}",
            f"-Dxmage.aiPuppeteer.password={config.password}",
        ]
    )

    env = {
        "XMAGE_AI_PUPPETEER": "1",
        "XMAGE_AI_PUPPETEER_USER": config.user,
        "XMAGE_AI_PUPPETEER_PASSWORD": config.password,
        "XMAGE_AI_PUPPETEER_SERVER": config.server,
        "XMAGE_AI_PUPPETEER_PORT": str(config.port),
        "XMAGE_AI_PUPPETEER_DISABLE_WHATS_NEW": "1",
        "XMAGE_AI_PUPPETEER_PLAYERS_CONFIG": config_json,
        "MAVEN_OPTS": jvm_args,
    }
    if config.match_time_limit:
        env["XMAGE_AI_PUPPETEER_MATCH_TIME_LIMIT"] = config.match_time_limit
    if config.match_buffer_time:
        env["XMAGE_AI_PUPPETEER_MATCH_BUFFER_TIME"] = config.match_buffer_time
    if config.custom_start_life:
        env["XMAGE_AI_PUPPETEER_CUSTOM_START_LIFE"] = str(config.custom_start_life)
    if config.skip_init_shuffling:
        env["XMAGE_AI_PUPPETEER_SKIP_INIT_SHUFFLING"] = "true"

    return pm.start_jvm_process(
        args=["mvn", "-q", "exec:java"],
        cwd=project_root / "Mage.Client",
        env=env,
        log_file=log_path,
    )


def start_potato_client(
    pm: ProcessManager,
    project_root: Path,
    config: Config,
    name: str,
    deck_path: str | None,
    log_path: Path,
    personality: str = "potato",
) -> subprocess.Popen:
    """Start an auto-responder bridge client (potato/staller)."""
    jvm_args_list = [
        config.jvm_bridge_opts,
        "-Xmx512m",
        f"-Dxmage.bridge.server={config.server}",
        f"-Dxmage.bridge.port={config.port}",
        f"-Dxmage.bridge.personality={personality}",
    ]

    jvm_args = " ".join(jvm_args_list)
    env = {"MAVEN_OPTS": jvm_args}

    # Pass values that may contain spaces as Maven CLI args (not in MAVEN_OPTS)
    # because MAVEN_OPTS gets shell-split by the mvn script.
    mvn_args = ["mvn", "-q", f"-Dxmage.bridge.username={name}"]
    if deck_path:
        resolved_path = project_root / deck_path
        mvn_args.append(f"-Dxmage.bridge.deck={resolved_path}")
    mvn_args.append("exec:java")

    return pm.start_jvm_process(
        args=mvn_args,
        cwd=project_root / "Mage.Client.Bridge",
        env=env,
        log_file=log_path,
    )


def start_sleepwalker_client(
    pm: ProcessManager,
    project_root: Path,
    config: Config,
    name: str,
    deck_path: str | None,
    log_path: Path,
) -> subprocess.Popen:
    """Start a sleepwalker client (Python MCP client + bridge in MCP mode).

    This spawns the sleepwalker.py script which in turn spawns the bridge.
    """
    env = {
        "PYTHONUNBUFFERED": "1",
    }

    args = [
        sys.executable,
        "-m",
        "puppeteer.sleepwalker",
        "--server",
        config.server,
        "--port",
        str(config.port),
        "--username",
        name,
        "--project-root",
        str(project_root),
    ]

    if deck_path:
        args.extend(["--deck", str(project_root / deck_path)])

    return pm.start_process(
        args=args,
        cwd=project_root,
        env=env,
        log_file=log_path,
    )


def start_replay_client(
    pm: ProcessManager,
    project_root: Path,
    config: Config,
    name: str,
    deck_path: str | None,
    script_path: str | None,
    log_path: Path,
    game_dir: Path | None = None,
) -> subprocess.Popen:
    """Start a replay client (Python MCP client + bridge, scripted tool calls).

    This spawns the replay.py script which in turn spawns the bridge.
    """
    env = {
        "PYTHONUNBUFFERED": "1",
    }

    args = [
        sys.executable,
        "-m",
        "puppeteer.replay",
        "--server",
        config.server,
        "--port",
        str(config.port),
        "--username",
        name,
        "--project-root",
        str(project_root),
    ]

    if deck_path:
        args.extend(["--deck", str(project_root / deck_path)])
    if script_path:
        args.extend(["--script", str(project_root / script_path)])
    if game_dir:
        args.extend(["--game-dir", str(game_dir)])

    return pm.start_process(
        args=args,
        cwd=project_root,
        env=env,
        log_file=log_path,
    )


def start_pilot_client(
    pm: ProcessManager,
    project_root: Path,
    config: Config,
    player: PilotPlayer,
    log_path: Path,
    game_dir: Path | None = None,
) -> subprocess.Popen:
    """Start a pilot client (LLM-powered game player via MCP).

    This spawns the pilot.py script which in turn spawns the bridge.
    """
    env = {
        "PYTHONUNBUFFERED": "1",
    }

    # Pass the provider-specific API key based on the player's configured provider.
    key_env = required_api_key_env(player.provider)
    api_key = os.environ.get(key_env, "")
    if api_key:
        env[key_env] = api_key

    args = [
        sys.executable,
        "-m",
        "puppeteer.pilot",
        "--server",
        config.server,
        "--port",
        str(config.port),
        "--username",
        player.name,
        "--project-root",
        str(project_root),
    ]

    if player.deck:
        args.extend(["--deck", str(project_root / player.deck)])
    if player.model:
        args.extend(["--model", player.model])
    if player.provider != DEFAULT_LLM_PROVIDER:
        args.extend(["--provider", player.provider])
    # System prompt is resolved from preset and is always required.
    # Personality suffix is fenced to make clear it only affects chat/narration,
    # not gameplay decisions.
    assert player.system_prompt, f"Pilot player {player.name} has no system_prompt (check preset)"
    effective_prompt = player.system_prompt
    if player.prompt_suffix:
        effective_prompt += (
            "\n\n## Chat Personality\n"
            "You have a chat personality described below. Use it to flavor your "
            "narration and trash-talk — be expressive, have fun with it, and "
            "react to your opponent's chat messages in character. But your actual "
            "gameplay decisions (card choices, attacks, blocks, targets, sequencing) "
            "must always be based on optimal Magic strategy. Never let the persona "
            "influence which play you choose.\n\n" + player.prompt_suffix
        )
    args.extend(["--system-prompt", effective_prompt])
    if player.max_interactions_per_turn is not None:
        args.extend(["--max-interactions-per-turn", str(player.max_interactions_per_turn)])
    if player.reasoning_effort:
        args.extend(["--reasoning-effort", player.reasoning_effort])
    if player.tools is not None:
        args.extend(["--tools", ",".join(player.tools)])
    if player.ignore_providers:
        args.extend(["--ignore-providers", ",".join(player.ignore_providers)])
    if player.provider_order:
        args.extend(["--provider-order", ",".join(player.provider_order)])
    if player.cache_control:
        args.extend(["--cache-control", json.dumps(player.cache_control)])
    if game_dir:
        args.extend(["--game-dir", str(game_dir)])

    return pm.start_process(
        args=args,
        cwd=project_root,
        env=env,
        log_file=log_path,
    )


def start_observer_client(
    pm: ProcessManager,
    project_root: Path,
    config: Config,
    log_path: Path,
    game_dir: Path | None = None,
) -> subprocess.Popen:
    """Start the observer spectator client.

    This client automatically requests hand permission from all players,
    making it suitable for Twitch broadcasting where viewers should see all hands.
    """
    # Pass resolved player config (with actual deck paths, not "random")
    config_json = config.get_players_config_json()

    jvm_args_list = [
        config.jvm_opens,
        config.jvm_rendering,
        "-Xmx1536m",
        "-Dxmage.aiPuppeteer.autoConnect=true",
        "-Dxmage.aiPuppeteer.autoStart=true",
        "-Dxmage.aiPuppeteer.disableWhatsNew=true",
        f"-Dxmage.aiPuppeteer.server={config.server}",
        f"-Dxmage.aiPuppeteer.port={config.port}",
        f"-Dxmage.aiPuppeteer.user={config.user}",
        f"-Dxmage.aiPuppeteer.password={config.password}",
    ]

    # Add game directory for cost file polling
    if game_dir:
        jvm_args_list.append(f"-Dxmage.observer.gameDir={game_dir}")

    # Add recording path if configured
    if config.record:
        resolved_game_dir = game_dir or (project_root / config.log_dir / f"game_{config.timestamp}").resolve()
        record_path = config.record_output or (resolved_game_dir / "recording.mov")
        jvm_args_list.append(f"-Dxmage.observer.record={record_path}")

    jvm_args = " ".join(jvm_args_list)

    env = {
        "XMAGE_AI_PUPPETEER": "1",
        "XMAGE_AI_PUPPETEER_USER": config.user,
        "XMAGE_AI_PUPPETEER_PASSWORD": config.password,
        "XMAGE_AI_PUPPETEER_SERVER": config.server,
        "XMAGE_AI_PUPPETEER_PORT": str(config.port),
        "XMAGE_AI_PUPPETEER_DISABLE_WHATS_NEW": "1",
        "XMAGE_AI_PUPPETEER_PLAYERS_CONFIG": config_json,
        "MAVEN_OPTS": jvm_args,
    }
    if config.match_time_limit:
        env["XMAGE_AI_PUPPETEER_MATCH_TIME_LIMIT"] = config.match_time_limit
    if config.match_buffer_time:
        env["XMAGE_AI_PUPPETEER_MATCH_BUFFER_TIME"] = config.match_buffer_time
    if config.custom_start_life:
        env["XMAGE_AI_PUPPETEER_CUSTOM_START_LIFE"] = str(config.custom_start_life)
    if config.skip_init_shuffling:
        env["XMAGE_AI_PUPPETEER_SKIP_INIT_SHUFFLING"] = "true"

    args = ["mvn", "-q", "exec:java"]

    # Auto-detect headless Linux and wrap with xvfb-run for virtual framebuffer.
    # This lets Swing render to a virtual X11 display so recording still works.
    if sys.platform == "linux" and "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ:
        xvfb = shutil.which("xvfb-run")
        assert xvfb is not None, (
            "Headless environment detected (no DISPLAY set) but xvfb-run is not installed. "
            "Install xvfb for your distribution (e.g. apt-get install xvfb or dnf install xorg-x11-server-Xvfb)."
        )
        args = [xvfb, "--auto-servernum", "--server-args=-screen 0 1920x1080x24", *args]
        logger.info("Headless environment detected — wrapping observer with xvfb-run")

    return pm.start_jvm_process(
        args=args,
        cwd=project_root / "Mage.Client.Observer",
        env=env,
        log_file=log_path,
    )


def _save_youtube_url(game_dir: Path, url: str) -> None:
    """Save YouTube URL to game_meta.json."""
    meta_path = game_dir / "game_meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        meta["youtube_url"] = url
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")


def _update_website_youtube_url(game_dir: Path, url: str, project_root: Path) -> None:
    """Patch the YouTube URL into the website game JSON and index if they exist."""
    game_id = game_dir.name
    website_games_dir = project_root / "website" / "public" / "games"

    # Update per-game JSON
    game_json = website_games_dir / f"{game_id}.json"
    if game_json.exists():
        data = json.loads(game_json.read_text())
        data["youtubeUrl"] = url
        game_json.write_text(json.dumps(data, indent=2))

    # Update index.json
    index_json = website_games_dir / "index.json"
    if index_json.exists():
        index = json.loads(index_json.read_text())
        for entry in index:
            if entry.get("id") == game_id:
                entry["youtubeUrl"] = url
                break
        index_json.write_text(json.dumps(index, indent=2))


@dataclass
class AnnotationFailure:
    """A game that was exported but failed annotation, pending user decision."""

    tmp_path: Path
    final_path: Path
    error: str
    game_id: str


def _attempt_annotation(gz_path: Path, project_root: Path, max_retries: int = 2) -> tuple[str | None, float]:
    """Try to annotate a game file, with automatic retries.

    Returns (None, cost) on success, or (error_message, 0.0) on failure.
    """
    last_error = ""
    for attempt in range(1 + max_retries):
        try:
            cost = _analyze_blunders(str(gz_path))
            return None, cost  # success
        except (BlunderAnalysisError, OpenAIError) as e:
            last_error = str(e)
            if attempt < max_retries:
                logger.warning("  Annotation attempt %d failed: %s", attempt + 1, e)
                logger.warning("  Retrying (%d/%d)...", attempt + 2, 1 + max_retries)
            else:
                logger.warning("  Annotation attempt %d failed: %s", attempt + 1, e)
    return last_error, 0.0


def _prompt_annotation_failure(game_id: str, error: str) -> str:
    """Ask the user what to do about a failed annotation.

    Returns "retry", "emit", or "skip".
    """
    logger.warning("  Annotation failed for %s: %s", game_id, error)
    while True:
        try:
            answer = input("  [r]etry / [e]mit without annotation / [s]kip? ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            logger.info("")
            return "skip"
        if answer in ("r", "retry"):
            return "retry"
        if answer in ("e", "emit"):
            return "emit"
        if answer in ("s", "skip"):
            return "skip"
        logger.info("  Unrecognized answer: %r", answer)


def _finalize_export(tmp_path: Path, final_path: Path) -> None:
    """Move exported game from temp location to final website path."""
    shutil.move(str(tmp_path), str(final_path))
    size_kb = final_path.stat().st_size // 1024
    logger.info("  Exported for website: %s (%d KB)", final_path, size_kb)


def resolve_annotation_failures(failures: list[AnnotationFailure], project_root: Path) -> None:
    """Prompt the user about each deferred annotation failure."""
    if not failures:
        return
    logger.info("  %d game(s) failed annotation:", len(failures))
    for failure in failures:
        while True:
            action = _prompt_annotation_failure(failure.game_id, failure.error)
            if action == "retry":
                err, _cost = _attempt_annotation(failure.tmp_path, project_root, max_retries=0)
                if err is None:
                    _finalize_export(failure.tmp_path, failure.final_path)
                    break
                failure.error = err
                continue  # re-prompt
            if action == "emit":
                _finalize_export(failure.tmp_path, failure.final_path)
                break
            # skip
            failure.tmp_path.unlink(missing_ok=True)
            logger.info("  Skipped %s", failure.game_id)
            break


def upload_and_export(
    game_dir: Path,
    project_root: Path,
    *,
    deferred_failures: list[AnnotationFailure] | None = None,
    post_game_failures: list[str] | None = None,
) -> float:
    """Upload recording to YouTube and export for website.

    Returns blunder analysis cost in USD.

    When deferred_failures is provided (batch mode), annotation failures
    are appended to it for resolution later instead of prompting immediately.

    When post_game_failures is provided, failure messages are appended
    for display in the final summary.
    """
    recording = game_dir / "recording.mov"
    game_id = game_dir.name

    # Upload to YouTube (only if we have a recording)
    if recording.exists():
        try:
            url = _upload_to_youtube(game_dir)
            if url:
                logger.info("  YouTube: %s", url)
                _save_youtube_url(game_dir, url)
                _update_website_youtube_url(game_dir, url, project_root)
        except (YouTubeUploadError, OSError, json.JSONDecodeError) as e:
            logger.warning("  YouTube upload failed: %s", e)
            if post_game_failures is not None:
                post_game_failures.append(f"{game_id}: YouTube upload failed: {e}")

    # Export for website — write to a temp file first, only move to final
    # location after annotation succeeds (or user explicitly chooses to emit).
    website_games_dir = project_root / "website" / "public" / "games"
    tmp_path = None
    final_path = None
    try:
        # Export to a temp dir so the final location stays clean until we're ready
        website_games_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=website_games_dir) as tmp_dir:
            tmp_export_path = _export_game(game_dir, Path(tmp_dir))
            # Move to a temp name in the real dir (for atomic rename later)
            final_path = website_games_dir / tmp_export_path.name
            tmp_path = website_games_dir / f".tmp_{tmp_export_path.name}"
            shutil.move(str(tmp_export_path), str(tmp_path))
    except (GameExportError, OSError) as e:
        logger.warning("  Website export failed: %s", e)
        if post_game_failures is not None:
            post_game_failures.append(f"{game_id}: Website export failed: {e}")
        return 0.0

    # Blunder analysis (requires OPENROUTER_API_KEY; skips already-analyzed games)
    if not os.environ.get("OPENROUTER_API_KEY"):
        # No API key — emit without annotation
        _finalize_export(tmp_path, final_path)
        return 0.0

    err, cost = _attempt_annotation(tmp_path, project_root)
    if err is None:
        # Annotation succeeded
        _finalize_export(tmp_path, final_path)
        return cost

    # Annotation failed after retries
    if deferred_failures is not None:
        # Batch mode: defer to end
        deferred_failures.append(AnnotationFailure(tmp_path, final_path, err, game_id))
        logger.info("  Deferred annotation failure for %s (will ask at end)", game_id)
        return 0.0

    # Interactive mode: prompt immediately
    while True:
        action = _prompt_annotation_failure(game_id, err)
        if action == "retry":
            err, cost = _attempt_annotation(tmp_path, project_root, max_retries=0)
            if err is None:
                _finalize_export(tmp_path, final_path)
                return cost
            continue  # re-prompt
        if action == "emit":
            if post_game_failures is not None:
                post_game_failures.append(f"{game_id}: Blunder analysis failed: {err}")
            _finalize_export(tmp_path, final_path)
            return 0.0
        # skip
        if post_game_failures is not None:
            post_game_failures.append(f"{game_id}: Blunder analysis failed (skipped): {err}")
        tmp_path.unlink(missing_ok=True)
        logger.info("  Skipped %s", game_id)
        return 0.0


@dataclass
class GameSession:
    """State for a single game within a parallel run."""

    index: int
    game_dir: Path
    config: Config
    spectator_proc: subprocess.Popen | None = None
    pilot_procs: list[tuple[str, subprocess.Popen]] = field(default_factory=list)


@dataclass
class OrchestratorRunResult:
    """Result of a programmatic orchestrator run."""

    exit_code: int
    sessions: list[GameSession] = field(default_factory=list)
    pilot_costs: dict[int, float] = field(default_factory=dict)
    blunder_costs: dict[int, float] = field(default_factory=dict)
    post_game_failures: list[str] = field(default_factory=list)


def _setup_game(
    index: int,
    num_games: int,
    base_config: Config,
    pm: ProcessManager,
    project_root: Path,
    log_dir: Path,
    timestamp: str,
    used_player_names: set[str] | None = None,
    cross_game_round_robin: list[tuple[str, ...]] | None = None,
    cross_game_format_picks: list[str] | None = None,
) -> GameSession:
    """Set up a single game: create dir, load config, start spectator + clients.

    For parallel runs (num_games > 1), each game gets a fresh Config with
    independent random resolution (different decks, presets, personalities).
    Games are started sequentially (staggered) so bridge clients join the
    correct table.
    """
    batch = num_games > 1
    game_label = f"Game {index + 1}/{num_games}: " if batch else ""

    # Create a fresh config for each game so random resolution is independent.
    # For single-game runs, reuse the base_config directly (already loaded).
    if batch:
        config_file = base_config.config_file
        if base_config.batch_config_files:
            assert index < len(base_config.batch_config_files), f"Missing batch config for game {index + 1}/{num_games}"
            config_file = base_config.batch_config_files[index]
        game_config = base_config.new_game_config(
            config_file=config_file,
            user=f"spectator{index + 1}",
            num_games=num_games,
            port=base_config.port,
            timestamp=timestamp,
        )
        game_config.load_config(
            cross_game_used_names=used_player_names,
            cross_game_round_robin=cross_game_round_robin,
            cross_game_format_picks=cross_game_format_picks,
        )
        # Each spectator needs a unique username on the server to avoid
        # session conflicts (the server invalidates the old session when a
        # new client connects with the same username).
        # Track all player names so later games can avoid duplicates.
        # Two bridge clients with the same XMage username create an
        # infinite disconnect/reconnect loop (same-host kick race).
        if used_player_names is not None:
            all_players = (
                game_config.pilot_players
                + game_config.potato_players
                + game_config.staller_players
                + game_config.sleepwalker_players
            )
            for p in all_players:
                assert p.name not in used_player_names, (
                    f"Duplicate player name {p.name!r} across parallel games — "
                    f"two bridge clients with the same XMage username will "
                    f"endlessly kick each other. Reduce num_games or use "
                    f"unique player names."
                )
                used_player_names.add(p.name)
    else:
        game_config = base_config

    # Create game directory
    suffix = f"_g{index + 1}" if batch else ""
    game_dir = log_dir / f"game_{timestamp}{suffix}"
    game_dir.mkdir(parents=True, exist_ok=True)

    # Write provenance manifest
    manifest: dict[str, str | list[str] | int | None] = {
        "timestamp": timestamp,
        "branch": _git("rev-parse --abbrev-ref HEAD", project_root),
        "commit": _git("rev-parse HEAD", project_root),
        "commit_log": _git("log --oneline -10", project_root).splitlines(),
        "command": sys.argv,
        "config_file": str(game_config.config_file) if game_config.config_file else None,
    }
    if batch:
        manifest["game_index"] = index + 1
        manifest["num_games"] = num_games
    (game_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    # Copy config into game directory for reference
    if game_config.config_file:
        shutil.copy2(game_config.config_file, game_dir / "config.json")

    # Resolve decks
    resolve_choice_decks(game_config.pilot_players, project_root, game_config.deck_type)
    game_config.resolve_random_decks(project_root)

    # Write game metadata
    _write_game_meta(game_dir, game_config, project_root)

    # Log paths
    spectator_log = game_dir / "spectator.log"
    logger.info("%sGame logs: %s", game_label, game_dir)
    logger.info("%sSpectator log: %s", game_label, spectator_log)
    if game_config.record:
        record_path = game_config.record_output or (game_dir / "recording.mov")
        logger.info("%sRecording to: %s", game_label, record_path)

    # Choose spectator client type
    if game_config.observer:
        logger.info("%sStarting observer spectator client...", game_label)
        start_spectator_client = start_observer_client
    else:
        start_spectator_client = start_gui_client

    # Start spectator
    spectator_proc = start_spectator_client(pm, project_root, game_config, spectator_log, game_dir=game_dir)

    session = GameSession(
        index=index,
        game_dir=game_dir,
        config=game_config,
        spectator_proc=spectator_proc,
    )

    # Count bridge clients
    bridge_count = (
        len(game_config.sleepwalker_players)
        + len(game_config.pilot_players)
        + len(game_config.replay_players)
        + len(game_config.potato_players)
        + len(game_config.staller_players)
    )

    try:
        if bridge_count > 0:
            _wait_for_spectator_table(spectator_log, spectator_proc, timeout=300)

            # Start bridge clients
            for sleepwalker_player in game_config.sleepwalker_players:
                log_path = game_dir / f"{sleepwalker_player.name}_mcp.log"
                logger.info("%sSleepwalker (%s) log: %s", game_label, sleepwalker_player.name, log_path)
                start_sleepwalker_client(
                    pm, project_root, game_config, sleepwalker_player.name, sleepwalker_player.deck, log_path
                )

            for pilot_player in game_config.pilot_players:
                log_path = game_dir / f"{pilot_player.name}_pilot.log"
                logger.info("%sPilot (%s) log: %s", game_label, pilot_player.name, log_path)
                proc = start_pilot_client(pm, project_root, game_config, pilot_player, log_path, game_dir=game_dir)
                session.pilot_procs.append((pilot_player.name, proc))

            for replay_player in game_config.replay_players:
                log_path = game_dir / f"{replay_player.name}_replay.log"
                logger.info("%sReplay (%s) log: %s", game_label, replay_player.name, log_path)
                proc = start_replay_client(
                    pm,
                    project_root,
                    game_config,
                    replay_player.name,
                    replay_player.deck,
                    replay_player.script,
                    log_path,
                    game_dir=game_dir,
                )
                session.pilot_procs.append((replay_player.name, proc))

            for potato_player in game_config.potato_players:
                log_path = game_dir / f"{potato_player.name}_mcp.log"
                logger.info("%sPotato (%s) log: %s", game_label, potato_player.name, log_path)
                start_potato_client(pm, project_root, game_config, potato_player.name, potato_player.deck, log_path)

            for staller_player in game_config.staller_players:
                log_path = game_dir / f"{staller_player.name}_mcp.log"
                logger.info("%sStaller (%s) log: %s", game_label, staller_player.name, log_path)
                start_potato_client(
                    pm,
                    project_root,
                    game_config,
                    staller_player.name,
                    staller_player.deck,
                    log_path,
                    personality="staller",
                )

            # In parallel mode, wait for the game to actually start (table leaves
            # WAITING state) before returning.  This prevents the next game's
            # bridge clients from joining this table by mistake.
            if batch:
                _wait_for_game_start(spectator_log, spectator_proc)
    except (TimeoutError, RuntimeError):
        # Clean up processes for this game before propagating the error.
        if spectator_proc.poll() is None:
            spectator_proc.terminate()
        for _, proc in session.pilot_procs:
            if proc.poll() is None:
                proc.terminate()
        raise

    return session


def _wait_for_all_games(
    sessions: list[GameSession],
    pm: ProcessManager,
    poll_interval: float = 2.0,
) -> dict[int, int]:
    """Wait for all parallel games to complete.

    Monitors spectators and pilots across all games.  If a pilot in any game
    fails (non-zero exit), that game's spectator is terminated.  Other games
    continue running.

    Returns a mapping of game index to spectator exit code (-1 if killed due
    to pilot failure).
    """
    results: dict[int, int] = {}
    active = list(sessions)

    while active:
        time.sleep(poll_interval)
        for session in list(active):
            assert session.spectator_proc is not None

            # Check spectator
            spectator_rc = session.spectator_proc.poll()
            if spectator_rc is not None:
                if spectator_rc != 0:
                    # Spectator crashed — kill pilot process trees (includes
                    # bridge children) so they don't continue playing an
                    # unrecorded game on the server.
                    game_label = f"Game {session.index + 1}"
                    logger.error("%s: spectator exited with code %s — aborting game.", game_label, spectator_rc)
                    for _name, pilot_proc in session.pilot_procs:
                        if pilot_proc.poll() is None:
                            kill_tree(pilot_proc.pid)
                results[session.index] = spectator_rc
                active.remove(session)
                continue

            # Check pilots for failure
            for name, pilot_proc in session.pilot_procs:
                pilot_rc = pilot_proc.poll()
                if pilot_rc is not None and pilot_rc != 0:
                    logger.error(
                        "Game %d: pilot '%s' exited with code %s — aborting game.",
                        session.index + 1,
                        name,
                        pilot_rc,
                    )
                    session.spectator_proc.terminate()
                    for _n, pp in session.pilot_procs:
                        if pp.poll() is None:
                            kill_tree(pp.pid)
                    results[session.index] = -1
                    active.remove(session)
                    break

    return results


def _finalize_game(
    session: GameSession,
    project_root: Path,
    spectator_rc: int,
    *,
    deferred_failures: list[AnnotationFailure] | None = None,
    post_game_failures: list[str] | None = None,
) -> tuple[float, float]:
    """Post-game processing for a single game session.

    Returns (pilot_cost, blunder_cost) in USD.
    """
    game_label = f"Game {session.index + 1}: " if session.config.num_games > 1 else ""
    _ensure_game_over_event(session.game_dir, spectator_rc)
    _write_error_log(session.game_dir)
    try:
        merge_game_log(session.game_dir)
        logger.info("  %sMerged game log: %s", game_label, session.game_dir / "game.jsonl")
    except (OSError, UnicodeError) as e:
        logger.warning("  %sFailed to merge game log: %s", game_label, e)
    pilot_cost = _print_game_summary(session.game_dir)
    if not session.config.skip_post_game_prompts:
        blunder_cost = upload_and_export(
            session.game_dir,
            project_root,
            deferred_failures=deferred_failures,
            post_game_failures=post_game_failures,
        )
        return pilot_cost, blunder_cost
    return pilot_cost, 0.0


def _check_regular_season_block(project_root: Path) -> str | None:
    """Return an error message if regular-season games should be blocked."""
    season_file = project_root / "data" / "season.json"
    if not season_file.exists():
        return None
    season_data = json.loads(season_file.read_text())
    phase = season_data.get("phase")
    if phase == "regular-season":
        return None
    season_num = season_data.get("current_season", "?")
    if phase == "tournament":
        return (
            f"Season {season_num} is in the tournament phase! Regular-season games are not allowed during tournaments."
        )
    if phase == "between-seasons":
        return (
            f"Season {season_num} has crowned a champion. "
            "Regular-season games remain blocked until the next season starts."
        )
    return f"Season {season_num} is in phase '{phase}'. Regular-season games are only allowed during regular season."


def run_orchestrator(config: Config, project_root: Path | None = None) -> OrchestratorRunResult:
    """Run one orchestrator job programmatically."""
    if project_root is None:
        project_root = Path.cwd().resolve()

    # Load player config early so we can check flags before heavy setup.
    config.load_config()

    # Block regular-season games outside regular season
    if not config.skip_post_game_prompts and not config.tournament_game:
        season_block = _check_regular_season_block(project_root)
        if season_block:
            logger.error(season_block)
            return OrchestratorRunResult(exit_code=2)
    pm = ProcessManager()
    port_reservation = None
    sessions: list[GameSession] = []
    batch = config.num_games > 1
    pilot_costs: dict[int, float] = {}
    blunder_costs: dict[int, float] = {}
    post_game_failures: list[str] = []

    try:
        # Validate parallel mode constraints
        if batch and config.record_output:
            logger.error("--record=PATH cannot be used with --games (use --record without a path instead)")
            return OrchestratorRunResult(exit_code=2)
        missing_llm_keys = _missing_llm_api_keys_for_run(config)
        if missing_llm_keys:
            logger.error("LLM players configured without required API keys:")
            for missing in missing_llm_keys:
                logger.error("  - %s", missing)
            logger.error("Set the required key(s) or use a non-LLM config (e.g. make run).")
            return OrchestratorRunResult(exit_code=2)

        # Set timestamp
        config.timestamp = datetime.now(_LOG_TIMESTAMP_TZ).strftime("%Y%m%d_%H%M%S")

        # Recording requires observer mode
        if config.record and not config.observer:
            logger.info("Recording requires observer mode, enabling --observer")
            config.observer = True

        # Create log directory
        log_dir = (project_root / config.log_dir).resolve()
        log_dir.mkdir(parents=True, exist_ok=True)

        # Compile if needed
        if config.skip_compile:
            logger.info("Skipping compilation (--skip-compile)")
        else:
            if not compile_project(project_root, observer=config.observer):
                logger.error("Compilation failed")
                return OrchestratorRunResult(exit_code=1)

            if config.observer:
                logger.info("Refreshing observer resources...")
                if not refresh_observer_resources(project_root):
                    logger.error("Failed to refresh observer resources")
                    return OrchestratorRunResult(exit_code=1)

        # Find available port
        logger.info("Finding available port starting from %d...", config.start_port)
        port_reservation = find_available_port(config.server, config.start_port)
        config.port = port_reservation.port
        logger.info("Using port %d", config.port)

        # Generate server config (lives in first game's dir for N=1,
        # or in the log_dir for parallel runs)
        if batch:
            server_config_path = log_dir / f"server_config_{config.timestamp}.xml"
            server_log = log_dir / f"server_{config.timestamp}.log"
        else:
            first_game_dir = log_dir / f"game_{config.timestamp}"
            first_game_dir.mkdir(parents=True, exist_ok=True)
            server_config_path = first_game_dir / "server_config.xml"
            server_log = first_game_dir / "server.log"

        modify_server_config(
            source=project_root / "Mage.Server" / "config" / "config.xml",
            destination=server_config_path,
            port=config.port,
        )

        logger.info("Server log: %s", server_log)

        # Remove stale H2 lock files left by previously killed server processes.
        # A leftover lock file blocks the new server from opening the card DB.
        # Skip when --skip-compile is set: the caller handles cleanup once
        # before spawning parallel instances (avoids deleting a sibling's lock).
        if not config.skip_compile:
            clean_stale_h2_locks(project_root)

        # Start server
        logger.info("Starting XMage server...")
        start_server(pm, project_root, config, server_config_path, server_log)

        if not wait_for_port(config.server, config.port, config.server_wait):
            logger.error("Server failed to start within %ds", config.server_wait)
            logger.error("Check %s for details", server_log)
            return OrchestratorRunResult(exit_code=1)

        # Server has bound the port — release the reservation lock
        port_reservation.release()
        port_reservation = None

        logger.info("Server is ready!")

        if config.config_file:
            logger.info("Using config: %s", config.config_file)

        if batch:
            logger.info("Starting %d parallel games...", config.num_games)

        # --- Per-game setup (staggered for parallel) ---
        # Track player names across games to prevent duplicate XMage
        # usernames, which cause an infinite disconnect/reconnect loop.
        used_player_names: set[str] = set()
        # Track round-robin matchups across games so each game in a batch
        # picks a different coverage-optimal pairing.
        cross_game_round_robin: list[tuple[str, ...]] = []
        # Track format picks across games so each game in a batch
        # spreads across formats when deckType is a list.
        cross_game_format_picks: list[str] = []
        for i in range(config.num_games):
            try:
                session = _setup_game(
                    i,
                    config.num_games,
                    config,
                    pm,
                    project_root,
                    log_dir,
                    config.timestamp,
                    used_player_names=used_player_names if batch else None,
                    cross_game_round_robin=cross_game_round_robin if batch else None,
                    cross_game_format_picks=cross_game_format_picks if batch else None,
                )
            except (TimeoutError, RuntimeError) as e:
                if not batch:
                    raise
                game_label = f"Game {i + 1}/{config.num_games}"
                logger.error("%s: failed to launch, skipping: %s", game_label, e)
                continue
            sessions.append(session)

        if batch and not sessions:
            logger.error("No games launched successfully")
            return OrchestratorRunResult(exit_code=1)

        # Bring the GUI window to the foreground on macOS (single game only)
        if not batch:
            bring_to_foreground_macos()

        # Update symlinks to point to the last game directory
        last_game_dir = sessions[-1].game_dir
        if config.config_file and not config.batch_config_files:
            last_link = log_dir / f"last-{config.run_tag}"
            last_link.unlink(missing_ok=True)
            last_link.symlink_to(last_game_dir.name)
        branch = _git("rev-parse --abbrev-ref HEAD", project_root)
        if branch:
            safe_branch = branch.replace("/", "-")
            branch_link = log_dir / f"last-branch-{safe_branch}"
            branch_link.unlink(missing_ok=True)
            branch_link.symlink_to(last_game_dir.name)

        # --- Wait for all games to complete ---
        if batch:
            results = _wait_for_all_games(sessions, pm)
            deferred: list[AnnotationFailure] = []
            for session in sessions:
                spectator_rc = results.get(session.index, -1)
                pilot_costs[session.index], blunder_costs[session.index] = _finalize_game(
                    session,
                    project_root,
                    spectator_rc,
                    deferred_failures=deferred,
                    post_game_failures=post_game_failures,
                )
            resolve_annotation_failures(deferred, project_root)
        else:
            # Single game: use existing wait logic
            session = sessions[0]
            assert session.spectator_proc is not None
            if session.pilot_procs:
                spectator_rc = _wait_with_pilot_monitoring(session.spectator_proc, session.pilot_procs, pm)
            else:
                spectator_rc = session.spectator_proc.wait()
            pilot_costs[session.index], blunder_costs[session.index] = _finalize_game(
                session, project_root, spectator_rc, post_game_failures=post_game_failures
            )

        _print_run_cost_summary(sessions, pilot_costs, blunder_costs)

        if post_game_failures:
            logger.error("")
            logger.error("!" * 60)
            logger.error("FAILURES")
            logger.error("!" * 60)
            for msg in post_game_failures:
                logger.error("  %s", msg)
            logger.error("!" * 60)

        if not config.skip_post_game_prompts:
            generate_all_website_data()
            logger.info("Website data regenerated")

        return OrchestratorRunResult(
            exit_code=0,
            sessions=sessions,
            pilot_costs=pilot_costs,
            blunder_costs=blunder_costs,
            post_game_failures=post_game_failures,
        )
    finally:
        # Release any held port reservations (safety net for early exits)
        if port_reservation is not None:
            port_reservation.release()
        # Always cleanup child processes, even on exceptions
        pm.cleanup()


def main() -> int:
    """Main orchestrator for game lifecycle management."""
    config = parse_args()
    setup_logging(debug=config.debug)
    if config.debug:
        os.environ["PUPPETEER_LOG_LEVEL"] = "DEBUG"
    return run_orchestrator(config).exit_code
