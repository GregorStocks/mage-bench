"""Main orchestrator for game lifecycle management."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from puppeteer.config import Config, PilotPlayer
from puppeteer.deck_choice import resolve_choice_decks
from puppeteer.game_log import merge_game_log, read_decklist
from puppeteer.harness_epoch import HARNESS_EPOCH
from puppeteer.llm_cost import DEFAULT_BASE_URL as DEFAULT_LLM_BASE_URL
from puppeteer.llm_cost import required_api_key_env
from puppeteer.port import PortReservation, find_available_overlay_port, find_available_port, wait_for_port
from puppeteer.process_manager import ProcessManager
from puppeteer.xml_config import modify_server_config

_SPECTATOR_TABLE_READY = "AI Puppeteer: waiting for"
_SPECTATOR_GAME_STARTED = "AI Puppeteer: all players joined"


def _git(cmd: str, cwd: Path) -> str:
    """Run a git command and return stripped output, or "" on failure."""
    try:
        return subprocess.check_output(
            f"git {cmd}",
            shell=True,
            cwd=cwd,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return ""


def _wait_for_spectator_table(log_path: Path, proc: subprocess.Popen, timeout: int = 300) -> None:
    """Block until the spectator log indicates the game table is ready.

    The streaming/GUI client logs a line containing ``AI Puppeteer: waiting
    for … bridge client(s)`` once it has created the table.  We poll the
    log file for that marker so headless clients aren't started before the
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
    before starting the next game's spectator.  This prevents headless clients
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
        base_url = player.base_url or DEFAULT_LLM_BASE_URL
        key_env = required_api_key_env(base_url)
        if not os.environ.get(key_env, "").strip():
            errors.append(f"{player.name} ({base_url}) requires {key_env}")
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
            return spectator_rc

        # Check all pilot processes
        for name, proc in pilot_procs:
            rc = proc.poll()
            if rc is not None and rc != 0:
                print(f"\nPilot '{name}' exited with code {rc} — aborting game.")
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
        ts = datetime.now().isoformat(timespec="milliseconds")
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
            for line in log_file.read_text().splitlines():
                if line.strip():
                    error_lines.append(f"[{log_file.stem}] {line}")
        except OSError:
            pass

    error_log = game_dir / "errors.log"
    if error_lines:
        error_log.write_text("\n".join(error_lines) + "\n")
        print(f"  Errors: {len(error_lines)} (see {error_log})")
    else:
        error_log.write_text("No errors detected.\n")


def _write_game_meta(game_dir: Path, config: Config, project_root: Path) -> None:
    """Write game_meta.json with player configs, decklists, format, and git info."""
    players = []
    all_players = [
        *((p, "pilot") for p in config.pilot_players),
        *((p, "sleepwalker") for p in config.sleepwalker_players),
        *((p, "potato") for p in config.potato_players),
        *((p, "staller") for p in config.staller_players),
        *((p, "cpu") for p in config.cpu_players),
    ]
    for player, ptype in all_players:
        entry = {"name": player.name, "type": ptype}
        if player.deck:
            entry["deck_path"] = player.deck
            deck_file = project_root / player.deck
            if deck_file.exists():
                entry["decklist"] = read_decklist(deck_file)
        if hasattr(player, "model") and player.model:
            entry["model"] = player.model
        if hasattr(player, "personality") and player.personality:
            entry["personality"] = player.personality
        if hasattr(player, "system_prompt") and player.system_prompt:
            entry["system_prompt"] = player.system_prompt
        if hasattr(player, "reasoning_effort") and player.reasoning_effort:
            entry["reasoning_effort"] = player.reasoning_effort
        players.append(entry)

    meta = {
        "timestamp": config.timestamp,
        "config": config.run_tag,
        "game_type": config.game_type,
        "deck_type": config.deck_type,
        "harness_epoch": HARNESS_EPOCH,
        "players": players,
        "git_branch": _git("rev-parse --abbrev-ref HEAD", project_root),
        "git_commit": _git("rev-parse --short HEAD", project_root),
    }
    (game_dir / "game_meta.json").write_text(json.dumps(meta, indent=2) + "\n")


def _print_game_summary(game_dir: Path) -> None:
    """Print a summary of game results and costs after the game ends."""
    print("\n" + "=" * 60)
    print("GAME SUMMARY")
    print("=" * 60)

    # Scan headless client logs for "Game over:" messages
    game_over_found = False
    for log_file in sorted(game_dir.glob("*_pilot.log")) + sorted(game_dir.glob("*_mcp.log")):
        try:
            text = log_file.read_text()
            for line in text.splitlines():
                if "Game over:" in line:
                    game_over_found = True
                    print(f"  {line.strip()}")
                    break  # Only first game_over per log (client may join next game)
        except OSError:
            pass

    # Fall back to game_events.jsonl (written by the streaming spectator).
    # CPU-only games have no headless client logs, but the spectator still
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
                            print(f"  {msg}")
                        elif reason not in ("timeout_or_killed", "spectator_crashed") and msg:
                            game_over_found = True
                            print(f"  Game over: {msg}")
                        break
            except OSError:
                pass

    if not game_over_found:
        print("  Game did not finish (killed or disconnected)")

    # Extract turn count from game events
    max_turn = 0
    events_file = game_dir / "game_events.jsonl"
    if events_file.exists():
        try:
            import re

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
        print(f"  Turns: {max_turn}")

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
    if cost_files or player_actions:
        print()
        total_cost = 0.0
        for cost_file in cost_files:
            try:
                data = json.loads(cost_file.read_text())
                cost = data.get("cost_usd", 0.0)
                player = cost_file.stem.replace("_cost", "")
                total_cost += cost
                actions_str = ""
                if player in player_actions:
                    actions_str = f" ({player_actions.pop(player)} actions)"
                print(f"  {player}: ${cost:.4f}{actions_str}")
            except (OSError, json.JSONDecodeError):
                pass
        # Print any players with actions but no cost file (shouldn't happen, but just in case)
        for player, actions in player_actions.items():
            print(f"  {player}: {actions} actions")
        print(f"  Total: ${total_cost:.4f}")

    print("=" * 60 + "\n")


def parse_args() -> Config:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="XMage AI Puppeteer")
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to player config JSON",
    )
    parser.add_argument(
        "--streaming",
        action="store_true",
        help="Launch the streaming spectator client (auto-requests hand permissions)",
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
        "--overlay",
        action="store_true",
        help="Enable local overlay server in streaming spectator (requires website-build)",
    )
    parser.add_argument(
        "--overlay-port",
        type=int,
        default=17888,
        help="Local overlay server port (default: 17888)",
    )
    parser.add_argument(
        "--overlay-host",
        type=str,
        default="127.0.0.1",
        help="Local overlay bind host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--games",
        type=int,
        default=1,
        help="Number of parallel games on the same server (default: 1)",
    )
    args = parser.parse_args()

    # Determine record output path
    record_output = None
    if args.record and args.record is not True:
        record_output = Path(args.record)

    config = Config(
        config_file=args.config,
        streaming=args.streaming,
        record=bool(args.record),
        record_output=record_output,
        overlay=args.overlay,
        overlay_port=args.overlay_port,
        overlay_host=args.overlay_host,
        num_games=args.games,
    )
    return config


def compile_project(project_root: Path, streaming: bool = False) -> bool:
    """Compile the project using Maven."""
    print("Compiling project...")
    modules = "Mage.Server,Mage.Client,Mage.Client.Headless"
    if streaming:
        modules += ",Mage.Client.Streaming"

    result = subprocess.run(
        [
            "mvn",
            "-q",
            "-DskipTests",
            "-pl",
            modules,
            "-am",
            "install",
        ],
        cwd=project_root,
    )
    return result.returncode == 0


def refresh_streaming_resources(project_root: Path) -> bool:
    """Refresh streaming client resources under target/classes.

    This keeps overlay static files in sync even when --skip-compile is used.
    """
    result = subprocess.run(
        [
            "mvn",
            "-q",
            "-pl",
            "Mage.Client.Streaming",
            "resources:resources",
        ],
        cwd=project_root,
    )
    return result.returncode == 0


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
            config.jvm_headless_opts,
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

    return pm.start_process(
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
) -> subprocess.Popen:
    """Start the GUI client."""
    # Pass resolved player config (with actual deck paths, not "random")
    config_json = config.get_players_config_json()

    jvm_args = " ".join(
        [
            config.jvm_opens,
            config.jvm_rendering,
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

    return pm.start_process(
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
    """Start an auto-responder headless client (potato/staller)."""
    jvm_args_list = [
        config.jvm_headless_opts,
        f"-Dxmage.headless.server={config.server}",
        f"-Dxmage.headless.port={config.port}",
        f"-Dxmage.headless.personality={personality}",
    ]

    jvm_args = " ".join(jvm_args_list)
    env = {"MAVEN_OPTS": jvm_args}

    # Pass values that may contain spaces as Maven CLI args (not in MAVEN_OPTS)
    # because MAVEN_OPTS gets shell-split by the mvn script.
    mvn_args = ["mvn", "-q", f"-Dxmage.headless.username={name}"]
    if deck_path:
        resolved_path = project_root / deck_path
        mvn_args.append(f"-Dxmage.headless.deck={resolved_path}")
    mvn_args.append("exec:java")

    return pm.start_process(
        args=mvn_args,
        cwd=project_root / "Mage.Client.Headless",
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
    import sys

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
    import os
    import sys

    env = {
        "PYTHONUNBUFFERED": "1",
    }

    # Pass the provider-specific API key based on player's base_url
    key_env = required_api_key_env(player.base_url or DEFAULT_LLM_BASE_URL)
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
    if player.base_url:
        args.extend(["--base-url", player.base_url])
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


def start_streaming_client(
    pm: ProcessManager,
    project_root: Path,
    config: Config,
    log_path: Path,
    game_dir: Path | None = None,
) -> subprocess.Popen:
    """Start the streaming spectator client.

    This client automatically requests hand permission from all players,
    making it suitable for Twitch streaming where viewers should see all hands.
    """
    # Pass resolved player config (with actual deck paths, not "random")
    config_json = config.get_players_config_json()

    jvm_args_list = [
        config.jvm_opens,
        config.jvm_rendering,
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
        jvm_args_list.append(f"-Dxmage.streaming.gameDir={game_dir}")

    # Add recording path if configured
    if config.record:
        resolved_game_dir = game_dir or (project_root / config.log_dir / f"game_{config.timestamp}").resolve()
        record_path = config.record_output or (resolved_game_dir / "recording.mov")
        jvm_args_list.append(f"-Dxmage.streaming.record={record_path}")

    # Add local overlay settings
    jvm_args_list.append(f"-Dxmage.streaming.overlay.enabled={'true' if config.overlay else 'false'}")
    jvm_args_list.append(f"-Dxmage.streaming.overlay.port={config.overlay_port}")
    jvm_args_list.append(f"-Dxmage.streaming.overlay.host={config.overlay_host}")
    webroot = project_root / "website" / "dist"
    jvm_args_list.append(f"-Dxmage.streaming.overlay.webroot={webroot}")

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

    return pm.start_process(
        args=["mvn", "-q", "exec:java"],
        cwd=project_root / "Mage.Client.Streaming",
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


def _maybe_upload_and_export(game_dir: Path, project_root: Path, *, auto_yes: bool = False) -> bool:
    """Prompt user to upload recording to YouTube and export for website.

    Returns True if the user answered "all" (auto-yes for remaining games).
    """
    recording = game_dir / "recording.mov"
    has_recording = recording.exists()

    if not auto_yes:
        prompt = "Upload to YouTube and export?" if has_recording else "Export for website?"
        while True:
            try:
                answer = input(f"{prompt} [y/N/all]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return False
            if answer in ("y", "yes"):
                break
            if answer in ("all", "a"):
                auto_yes = True
                break
            if answer in ("n", "no", ""):
                return False
            print(f"  Unrecognized answer: {answer!r} — please enter y, n, or all")

    # Upload to YouTube (only if we have a recording)
    if has_recording:
        try:
            sys.path.insert(0, str(project_root / "scripts"))
            from upload_youtube import upload_to_youtube

            url = upload_to_youtube(game_dir)
            if url:
                print(f"  YouTube: {url}")
                _save_youtube_url(game_dir, url)
                _update_website_youtube_url(game_dir, url, project_root)
        except ImportError:
            print("  Warning: YouTube upload requires google-api-python-client and google-auth-oauthlib")
            print("  Run: cd puppeteer && uv sync")
        except Exception as e:
            print(f"  Warning: YouTube upload failed: {e}")

    # Export for website
    output_path = None
    try:
        from export_game import export_game

        website_games_dir = project_root / "website" / "public" / "games"
        output_path = export_game(game_dir, website_games_dir)
        size_kb = output_path.stat().st_size // 1024
        print(f"  Exported for website: {output_path} ({size_kb} KB)")
    except Exception as e:
        print(f"  Warning: website export failed: {e}")

    # Blunder analysis (requires OPENROUTER_API_KEY; skips already-analyzed games)
    if output_path and os.environ.get("OPENROUTER_API_KEY"):
        try:
            sys.path.insert(0, str(project_root / "scripts" / "analysis"))
            from blunder_analysis import main as analyze_blunders

            analyze_blunders(str(output_path))
        except Exception as e:
            print(f"  Warning: blunder analysis failed: {e}")

    return auto_yes


@dataclass
class GameSession:
    """State for a single game within a parallel run."""

    index: int
    game_dir: Path
    config: Config
    spectator_proc: subprocess.Popen | None = None
    pilot_procs: list[tuple[str, subprocess.Popen]] = field(default_factory=list)
    overlay_reservation: PortReservation | None = None


def _setup_game(
    index: int,
    num_games: int,
    base_config: Config,
    pm: ProcessManager,
    project_root: Path,
    log_dir: Path,
    timestamp: str,
    used_player_names: set[str] | None = None,
) -> GameSession:
    """Set up a single game: create dir, load config, start spectator + clients.

    For parallel runs (num_games > 1), each game gets a fresh Config with
    independent random resolution (different decks, presets, personalities).
    Games are started sequentially (staggered) so headless clients join the
    correct table.
    """
    batch = num_games > 1
    game_label = f"Game {index + 1}/{num_games}: " if batch else ""

    # Create a fresh config for each game so random resolution is independent.
    # For single-game runs, reuse the base_config directly (already loaded).
    if batch:
        game_config = Config(
            config_file=base_config.config_file,
            streaming=base_config.streaming,
            record=base_config.record,
            overlay=False,  # Overlay disabled for parallel mode
            overlay_port=base_config.overlay_port,
            overlay_host=base_config.overlay_host,
            num_games=num_games,
        )
        game_config.load_config(cross_game_used_names=used_player_names)
        game_config.port = base_config.port
        game_config.timestamp = timestamp
        # Each spectator needs a unique username on the server to avoid
        # session conflicts (the server invalidates the old session when a
        # new client connects with the same username).
        game_config.user = f"spectator{index + 1}"
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
    manifest = {
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

    # Overlay port reservation (single-game only)
    overlay_reservation: PortReservation | None = None
    if not batch and game_config.streaming and game_config.overlay:
        requested_overlay_port = game_config.overlay_port
        overlay_reservation = find_available_overlay_port(requested_overlay_port)
        game_config.overlay_port = overlay_reservation.port
        if game_config.overlay_port != requested_overlay_port:
            print(f"Overlay port {requested_overlay_port} unavailable, using {game_config.overlay_port}")

    # Log paths
    spectator_log = game_dir / "spectator.log"
    print(f"{game_label}Game logs: {game_dir}")
    print(f"{game_label}Spectator log: {spectator_log}")
    if game_config.record:
        record_path = game_config.record_output or (game_dir / "recording.mov")
        print(f"{game_label}Recording to: {record_path}")
    if game_config.streaming and game_config.overlay:
        base = f"http://{game_config.overlay_host}:{game_config.overlay_port}"
        print(f"{game_label}Overlay API: {base}/api/state")
        print(f"{game_label}Live viewer: {base}/live")
        print(f"{game_label}OBS source:  {base}/live?positions=1&obs=1")

    # Choose spectator client type
    if game_config.streaming:
        print(f"{game_label}Starting streaming spectator client...")
        start_spectator_client = start_streaming_client
    else:
        start_spectator_client = start_gui_client

    # Start spectator
    if game_config.streaming:
        spectator_proc = start_spectator_client(pm, project_root, game_config, spectator_log, game_dir=game_dir)
    else:
        spectator_proc = start_spectator_client(pm, project_root, game_config, spectator_log)

    session = GameSession(
        index=index,
        game_dir=game_dir,
        config=game_config,
        spectator_proc=spectator_proc,
        overlay_reservation=overlay_reservation,
    )

    # Count headless clients
    headless_count = (
        len(game_config.sleepwalker_players)
        + len(game_config.pilot_players)
        + len(game_config.potato_players)
        + len(game_config.staller_players)
    )

    try:
        if headless_count > 0:
            _wait_for_spectator_table(spectator_log, spectator_proc, timeout=300)

            # Release overlay reservation now that spectator has bound it
            if overlay_reservation is not None:
                overlay_reservation.release()
                session.overlay_reservation = None

            # Start headless clients
            for player in game_config.sleepwalker_players:
                log_path = game_dir / f"{player.name}_mcp.log"
                print(f"{game_label}Sleepwalker ({player.name}) log: {log_path}")
                start_sleepwalker_client(pm, project_root, game_config, player.name, player.deck, log_path)

            for player in game_config.pilot_players:
                log_path = game_dir / f"{player.name}_pilot.log"
                print(f"{game_label}Pilot ({player.name}) log: {log_path}")
                proc = start_pilot_client(pm, project_root, game_config, player, log_path, game_dir=game_dir)
                session.pilot_procs.append((player.name, proc))

            for player in game_config.potato_players:
                log_path = game_dir / f"{player.name}_mcp.log"
                print(f"{game_label}Potato ({player.name}) log: {log_path}")
                start_potato_client(pm, project_root, game_config, player.name, player.deck, log_path)

            for player in game_config.staller_players:
                log_path = game_dir / f"{player.name}_mcp.log"
                print(f"{game_label}Staller ({player.name}) log: {log_path}")
                start_potato_client(
                    pm, project_root, game_config, player.name, player.deck, log_path, personality="staller"
                )

            # In parallel mode, wait for the game to actually start (table leaves
            # WAITING state) before returning.  This prevents the next game's
            # headless clients from joining this table by mistake.
            if batch:
                _wait_for_game_start(spectator_log, spectator_proc)
        else:
            if overlay_reservation is not None:
                overlay_reservation.release()
                session.overlay_reservation = None
    except (TimeoutError, RuntimeError):
        # Clean up processes for this game before propagating the error.
        if spectator_proc.poll() is None:
            spectator_proc.terminate()
        for _, proc in session.pilot_procs:
            if proc.poll() is None:
                proc.terminate()
        if overlay_reservation is not None:
            overlay_reservation.release()
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
                results[session.index] = spectator_rc
                active.remove(session)
                continue

            # Check pilots for failure
            for name, pilot_proc in session.pilot_procs:
                pilot_rc = pilot_proc.poll()
                if pilot_rc is not None and pilot_rc != 0:
                    print(f"\nGame {session.index + 1}: pilot '{name}' exited with code {pilot_rc} — aborting game.")
                    session.spectator_proc.terminate()
                    results[session.index] = -1
                    active.remove(session)
                    break

    return results


def _finalize_game(session: GameSession, project_root: Path, spectator_rc: int, *, auto_yes: bool = False) -> bool:
    """Post-game processing for a single game session.

    Returns True if the user chose "all" (auto-yes for remaining games).
    """
    game_label = f"Game {session.index + 1}: " if session.config.num_games > 1 else ""
    _ensure_game_over_event(session.game_dir, spectator_rc)
    _write_error_log(session.game_dir)
    try:
        merge_game_log(session.game_dir)
        print(f"  {game_label}Merged game log: {session.game_dir / 'game.jsonl'}")
    except Exception as e:
        print(f"  {game_label}Warning: failed to merge game log: {e}")
    _print_game_summary(session.game_dir)
    if not session.config.skip_post_game_prompts:
        auto_yes = _maybe_upload_and_export(session.game_dir, project_root, auto_yes=auto_yes)
    return auto_yes


def main() -> int:
    """Main orchestrator for game lifecycle management."""
    config = parse_args()
    project_root = Path.cwd().resolve()
    pm = ProcessManager()
    port_reservation = None
    sessions: list[GameSession] = []
    batch = config.num_games > 1

    try:
        # Validate parallel mode constraints
        if batch and config.record_output:
            print("ERROR: --record=PATH cannot be used with --games (use --record without a path instead)")
            return 2

        # Load player config as early as possible so invalid LLM setup fails fast.
        config.load_config()
        missing_llm_keys = _missing_llm_api_keys(config)
        if missing_llm_keys:
            print("ERROR: LLM players configured without required API keys:")
            for missing in missing_llm_keys:
                print(f"  - {missing}")
            print("Set the required key(s) or use a non-LLM config (e.g. make run-dumb).")
            return 2

        # Set timestamp
        config.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Recording requires streaming mode
        if config.record and not config.streaming:
            print("Recording requires streaming mode, enabling --streaming")
            config.streaming = True

        # Parallel mode: disable overlay (each streaming client would need its
        # own port; not worth the complexity for batch eval)
        if batch:
            config.overlay = False

        # Create log directory
        log_dir = (project_root / config.log_dir).resolve()
        log_dir.mkdir(parents=True, exist_ok=True)

        # Compile if needed
        if not compile_project(project_root, streaming=config.streaming):
            print("ERROR: Compilation failed")
            return 1

        if config.streaming:
            print("Refreshing streaming resources...")
            if not refresh_streaming_resources(project_root):
                print("ERROR: Failed to refresh streaming resources")
                return 1

        # Find available port
        print(f"Finding available port starting from {config.start_port}...")
        port_reservation = find_available_port(config.server, config.start_port)
        config.port = port_reservation.port
        print(f"Using port {config.port}")

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

        print(f"Server log: {server_log}")

        # Start server
        print("Starting XMage server...")
        start_server(pm, project_root, config, server_config_path, server_log)

        if not wait_for_port(config.server, config.port, config.server_wait):
            print(f"ERROR: Server failed to start within {config.server_wait}s")
            print(f"Check {server_log} for details")
            return 1

        # Server has bound the port — release the reservation lock
        port_reservation.release()
        port_reservation = None

        print("Server is ready!")

        if config.config_file:
            print(f"Using config: {config.config_file}")

        if batch:
            print(f"Starting {config.num_games} parallel games...")

        # --- Per-game setup (staggered for parallel) ---
        # Track player names across games to prevent duplicate XMage
        # usernames, which cause an infinite disconnect/reconnect loop.
        used_player_names: set[str] = set()
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
                )
            except (TimeoutError, RuntimeError) as e:
                if not batch:
                    raise
                game_label = f"Game {i + 1}/{config.num_games}"
                print(f"\n{game_label}: failed to launch, skipping: {e}")
                continue
            sessions.append(session)

        if batch and not sessions:
            print("ERROR: No games launched successfully")
            return 1

        # Bring the GUI window to the foreground on macOS (single game only)
        if not batch:
            bring_to_foreground_macos()

        # Update symlinks to point to the last game directory
        last_game_dir = sessions[-1].game_dir
        if config.config_file:
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
            upload_all = False
            for session in sessions:
                spectator_rc = results.get(session.index, -1)
                upload_all = _finalize_game(session, project_root, spectator_rc, auto_yes=upload_all)
        else:
            # Single game: use existing wait logic
            session = sessions[0]
            assert session.spectator_proc is not None
            if session.pilot_procs:
                spectator_rc = _wait_with_pilot_monitoring(session.spectator_proc, session.pilot_procs, pm)
            else:
                spectator_rc = session.spectator_proc.wait()
            _finalize_game(session, project_root, spectator_rc)

        return 0
    finally:
        # Release any held port reservations (safety net for early exits)
        if port_reservation is not None:
            port_reservation.release()
        # Release any overlay reservations held by game sessions
        for session in sessions:
            if session.overlay_reservation is not None:
                session.overlay_reservation.release()
        # Always cleanup child processes, even on exceptions
        pm.cleanup()
