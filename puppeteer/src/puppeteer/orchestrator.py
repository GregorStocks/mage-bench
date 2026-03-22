"""Main orchestrator for game lifecycle management."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from magebench.leaderboard.website_data import generate_all_website_data
from puppeteer.batch_coordination import GameSession, finalize_game, setup_game, wait_for_all_games
from puppeteer.config import Config
from puppeteer.game_finalization import (
    print_run_cost_summary,
    run_git,
)
from puppeteer.game_processes import (
    bring_to_foreground_macos,
    start_server,
    wait_with_pilot_monitoring,
)
from puppeteer.llm_cost import DEFAULT_LLM_PROVIDER, required_api_key_env
from puppeteer.log import get_logger, setup_logging
from puppeteer.port import find_available_port, wait_for_port
from puppeteer.post_game_analysis import (
    AnnotationFailure,
    resolve_annotation_failures,
)
from puppeteer.process_manager import ProcessManager, jvm_oom_preexec_fn
from puppeteer.xml_config import modify_server_config

logger = get_logger(__name__)

_LOG_TIMESTAMP_TZ = ZoneInfo("America/Los_Angeles")


def _missing_llm_api_keys(config: Config) -> list[str]:
    """Return validation errors for LLM players missing required API keys."""
    errors: list[str] = []
    for player in config.pilot_players:
        configured_provider = player.provider
        provider = configured_provider or DEFAULT_LLM_PROVIDER
        try:
            key_env = required_api_key_env(configured_provider)
        except ValueError as exc:
            errors.append(f"{player.name} ({provider}): {exc}")
            continue
        api_key = os.environ.get(key_env)
        if not api_key or not api_key.strip():
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


def parse_args() -> Config:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="XMage AI Puppeteer")
    parser.add_argument("--config", type=Path, help="Path to player config JSON")
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
        for index, item in enumerate(manifest):
            assert isinstance(item, str) and item, (
                f"Batch config manifest entry {index} must be a non-empty string path"
            )
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
    *,
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


@dataclass
class OrchestratorRunResult:
    """Result of a programmatic orchestrator run."""

    exit_code: int
    sessions: list[GameSession] = field(default_factory=list)
    pilot_costs: dict[int, float] = field(default_factory=dict)
    blunder_costs: dict[int, float] = field(default_factory=dict)
    post_game_failures: list[str] = field(default_factory=list)


def run_orchestrator(config: Config, project_root: Path | None = None) -> OrchestratorRunResult:
    """Run one orchestrator job programmatically."""
    if project_root is None:
        project_root = Path.cwd().resolve()

    config.load_config()

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

        config.timestamp = datetime.now(_LOG_TIMESTAMP_TZ).strftime("%Y%m%d_%H%M%S")

        if config.record and not config.observer:
            logger.info("Recording requires observer mode, enabling --observer")
            config.observer = True

        log_dir = (project_root / config.log_dir).resolve()
        log_dir.mkdir(parents=True, exist_ok=True)

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

        logger.info("Finding available port starting from %d...", config.start_port)
        port_reservation = find_available_port(config.start_port)
        config.port = port_reservation.port
        logger.info("Using port %d", config.port)

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

        if not config.skip_compile:
            clean_stale_h2_locks(project_root)

        logger.info("Starting XMage server...")
        start_server(pm, project_root, config, server_config_path, server_log)

        if not wait_for_port(config.server, config.port, config.server_wait):
            logger.error("Server failed to start within %ds", config.server_wait)
            logger.error("Check %s for details", server_log)
            return OrchestratorRunResult(exit_code=1)

        port_reservation.release()
        port_reservation = None
        logger.info("Server is ready!")

        if config.config_file:
            logger.info("Using config: %s", config.config_file)
        if batch:
            logger.info("Starting %d parallel games...", config.num_games)

        used_player_names: set[str] = set()
        cross_game_round_robin: list[tuple[str, ...]] = []
        cross_game_format_picks: list[str] = []
        for index in range(config.num_games):
            try:
                session = setup_game(
                    index,
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
            except (TimeoutError, RuntimeError) as exc:
                if not batch:
                    raise
                game_label = f"Game {index + 1}/{config.num_games}"
                logger.error("%s: failed to launch, skipping: %s", game_label, exc)
                continue
            sessions.append(session)

        if batch and not sessions:
            logger.error("No games launched successfully")
            return OrchestratorRunResult(exit_code=1)

        if not batch:
            bring_to_foreground_macos()

        last_game_dir = sessions[-1].game_dir
        if config.config_file and not config.batch_config_files:
            last_link = log_dir / f"last-{config.run_tag}"
            last_link.unlink(missing_ok=True)
            last_link.symlink_to(last_game_dir.name)
        branch = run_git("rev-parse --abbrev-ref HEAD", project_root)
        if branch:
            safe_branch = branch.replace("/", "-")
            branch_link = log_dir / f"last-branch-{safe_branch}"
            branch_link.unlink(missing_ok=True)
            branch_link.symlink_to(last_game_dir.name)

        if batch:
            results = wait_for_all_games(sessions)
            deferred: list[AnnotationFailure] = []
            for session in sessions:
                spectator_rc = results.get(session.index, -1)
                pilot_costs[session.index], blunder_costs[session.index] = finalize_game(
                    session,
                    project_root,
                    spectator_rc,
                    deferred_failures=deferred,
                    post_game_failures=post_game_failures,
                )
            resolve_annotation_failures(deferred)
        else:
            session = sessions[0]
            assert session.spectator_proc is not None
            if session.pilot_procs:
                spectator_rc = wait_with_pilot_monitoring(session.spectator_proc, session.pilot_procs, pm)
            else:
                spectator_rc = session.spectator_proc.wait()
            pilot_costs[session.index], blunder_costs[session.index] = finalize_game(
                session,
                project_root,
                spectator_rc,
                post_game_failures=post_game_failures,
            )

        print_run_cost_summary(sessions, pilot_costs, blunder_costs)

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
        if port_reservation is not None:
            port_reservation.release()
        pm.cleanup()


def main() -> int:
    """Main orchestrator for game lifecycle management."""
    config = parse_args()
    setup_logging(debug=config.debug)
    if config.debug:
        os.environ["PUPPETEER_LOG_LEVEL"] = "DEBUG"
    return run_orchestrator(config).exit_code
