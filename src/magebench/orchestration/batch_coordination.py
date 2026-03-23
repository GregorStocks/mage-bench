"""Per-game setup and batch coordination helpers for orchestrator runs."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from magebench.orchestration.game_finalization import (
    ensure_game_over_event,
    print_game_summary,
    run_git,
    write_error_log,
    write_game_meta,
)
from magebench.orchestration.game_processes import (
    start_gui_client,
    start_observer_client,
    start_pilot_client,
    start_replay_client,
    start_sleepwalker_client,
    wait_for_game_start,
    wait_for_spectator_table,
)
from magebench.orchestration.post_game_analysis import (
    AnnotationFailure,
    upload_and_export,
)
from puppeteer.config import Config
from puppeteer.deck_choice import resolve_choice_decks
from puppeteer.game_log import merge_game_log
from puppeteer.log import get_logger
from puppeteer.process_manager import ProcessManager, kill_tree

logger = get_logger(__name__)


@dataclass
class GameSession:
    """State for a single game within a parallel run."""

    index: int
    game_dir: Path
    config: Config
    spectator_proc: subprocess.Popen | None = None
    pilot_procs: list[tuple[str, subprocess.Popen]] = field(default_factory=list)


def setup_game(
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
    """Set up a single game: create dir, load config, start spectator and clients."""
    batch = num_games > 1
    game_label = f"Game {index + 1}/{num_games}: " if batch else ""

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
        if used_player_names is not None:
            all_players = game_config.pilot_players + game_config.sleepwalker_players
            for player in all_players:
                assert player.name not in used_player_names, (
                    f"Duplicate player name {player.name!r} across parallel games — "
                    f"two bridge clients with the same XMage username will "
                    f"endlessly kick each other. Reduce num_games or use "
                    f"unique player names."
                )
                used_player_names.add(player.name)
    else:
        game_config = base_config

    suffix = f"_g{index + 1}" if batch else ""
    game_dir = log_dir / f"game_{timestamp}{suffix}"
    game_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, str | list[str] | int | None] = {
        "timestamp": timestamp,
        "branch": run_git("rev-parse --abbrev-ref HEAD", project_root),
        "commit": run_git("rev-parse HEAD", project_root),
        "commit_log": run_git("log --oneline -10", project_root).splitlines(),
        "command": sys.argv,
        "config_file": str(game_config.config_file) if game_config.config_file else None,
    }
    if batch:
        manifest["game_index"] = index + 1
        manifest["num_games"] = num_games
    (game_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    if game_config.config_file:
        shutil.copy2(game_config.config_file, game_dir / "config.json")

    resolve_choice_decks(game_config.pilot_players, project_root, game_config.deck_type)
    game_config.resolve_random_decks(project_root)
    write_game_meta(game_dir, game_config, project_root)

    spectator_log = game_dir / "spectator.log"
    logger.info("%sGame logs: %s", game_label, game_dir)
    logger.info("%sSpectator log: %s", game_label, spectator_log)
    if game_config.record:
        record_path = game_config.record_output or (game_dir / "recording.mov")
        logger.info("%sRecording to: %s", game_label, record_path)

    if game_config.observer:
        logger.info("%sStarting observer spectator client...", game_label)
        start_spectator_client = start_observer_client
    else:
        start_spectator_client = start_gui_client

    spectator_proc = start_spectator_client(pm, project_root, game_config, spectator_log, game_dir=game_dir)
    session = GameSession(
        index=index,
        game_dir=game_dir,
        config=game_config,
        spectator_proc=spectator_proc,
    )

    bridge_count = (
        len(game_config.sleepwalker_players) + len(game_config.pilot_players) + len(game_config.replay_players)
    )

    try:
        if bridge_count > 0:
            wait_for_spectator_table(spectator_log, spectator_proc, timeout=300)

            for sleepwalker_player in game_config.sleepwalker_players:
                log_path = game_dir / f"{sleepwalker_player.name}_mcp.log"
                logger.info(
                    "%sSleepwalker (%s) log: %s",
                    game_label,
                    sleepwalker_player.name,
                    log_path,
                )
                start_sleepwalker_client(
                    pm,
                    project_root,
                    game_config,
                    sleepwalker_player.name,
                    sleepwalker_player.deck,
                    log_path,
                )

            for pilot_player in game_config.pilot_players:
                log_path = game_dir / f"{pilot_player.name}_pilot.log"
                logger.info("%sPilot (%s) log: %s", game_label, pilot_player.name, log_path)
                proc = start_pilot_client(
                    pm,
                    project_root,
                    game_config,
                    pilot_player,
                    log_path,
                    game_dir=game_dir,
                )
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

            if batch:
                wait_for_game_start(spectator_log, spectator_proc)
    except (TimeoutError, RuntimeError):
        if spectator_proc.poll() is None:
            spectator_proc.terminate()
        for _, proc in session.pilot_procs:
            if proc.poll() is None:
                proc.terminate()
        raise

    return session


def wait_for_all_games(
    sessions: list[GameSession],
    poll_interval: float = 2.0,
) -> dict[int, int]:
    """Wait for all parallel games to complete."""
    results: dict[int, int] = {}
    active = list(sessions)

    while active:
        time.sleep(poll_interval)
        for session in list(active):
            assert session.spectator_proc is not None

            spectator_rc = session.spectator_proc.poll()
            if spectator_rc is not None:
                if spectator_rc != 0:
                    game_label = f"Game {session.index + 1}"
                    logger.error(
                        "%s: spectator exited with code %s — aborting game.",
                        game_label,
                        spectator_rc,
                    )
                    for _name, pilot_proc in session.pilot_procs:
                        if pilot_proc.poll() is None:
                            kill_tree(pilot_proc.pid)
                results[session.index] = spectator_rc
                active.remove(session)
                continue

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
                    for _pilot_name, proc in session.pilot_procs:
                        if proc.poll() is None:
                            kill_tree(proc.pid)
                    results[session.index] = -1
                    active.remove(session)
                    break

    return results


def finalize_game(
    session: GameSession,
    project_root: Path,
    spectator_rc: int,
    *,
    deferred_failures: list[AnnotationFailure] | None = None,
    post_game_failures: list[str] | None = None,
) -> tuple[float, float]:
    """Run post-game processing for a single game session."""
    game_label = f"Game {session.index + 1}: " if session.config.num_games > 1 else ""
    ensure_game_over_event(session.game_dir, spectator_rc)
    write_error_log(session.game_dir)
    try:
        merge_game_log(session.game_dir)
        logger.info("  %sMerged game log: %s", game_label, session.game_dir / "game.jsonl")
    except (OSError, UnicodeError) as exc:
        logger.warning("  %sFailed to merge game log: %s", game_label, exc)

    pilot_cost = print_game_summary(session.game_dir)
    if not session.config.skip_post_game_prompts:
        blunder_cost = upload_and_export(
            session.game_dir,
            project_root,
            deferred_failures=deferred_failures,
            post_game_failures=post_game_failures,
        )
        return pilot_cost, blunder_cost
    return pilot_cost, 0.0
