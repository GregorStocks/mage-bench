"""Log writing and summary helpers for orchestrator runs."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from puppeteer.config import Config, PilotPlayer, Player
from puppeteer.game_log import read_decklist
from puppeteer.harness_epoch import HARNESS_EPOCH
from puppeteer.log import get_logger

if TYPE_CHECKING:
    from magebench.orchestration.batch_coordination import GameSession

logger = get_logger(__name__)

_LOG_TIMESTAMP_TZ = ZoneInfo("America/Los_Angeles")


def run_git(cmd: str, cwd: Path) -> str:
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


def ensure_game_over_event(game_dir: Path, spectator_exit_code: int = -1) -> None:
    """Append a game_over event to game_events.jsonl if one is missing."""
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


def write_error_log(game_dir: Path) -> None:
    """Combine per-player error logs into a unified errors.log."""
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


def write_game_meta(game_dir: Path, config: Config, project_root: Path) -> None:
    """Write game metadata with player configs, decklists, and git info."""
    assert config.game_type, "game_meta requires non-empty config.game_type"
    assert config.deck_type, "game_meta requires non-empty config.deck_type"
    players = []
    all_players: list[tuple[Player, str]] = [
        *((p, "pilot") for p in config.pilot_players),
        *((p, "sleepwalker") for p in config.sleepwalker_players),
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
            assert player.model, f"Pilot player {player.name} has no model (check preset)"
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
        "git_branch": run_git("rev-parse --abbrev-ref HEAD", project_root),
        "git_commit": run_git("rev-parse --short HEAD", project_root),
    }
    if config.tournament_game:
        meta["tournament_game"] = True
    (game_dir / "game_meta.json").write_text(json.dumps(meta, indent=2) + "\n")


def print_game_summary(game_dir: Path) -> float:
    """Print a summary of game results and costs after the game ends."""
    logger.info("=" * 60)
    logger.info("GAME SUMMARY")
    logger.info("=" * 60)

    game_over_found = False
    for log_file in sorted(game_dir.glob("*_pilot.log")) + sorted(game_dir.glob("*_mcp.log")):
        try:
            text = log_file.read_text()
            for line in text.splitlines():
                if "Game over:" in line:
                    game_over_found = True
                    logger.info("  %s", line.strip())
                    break
        except OSError:
            pass

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
                        reason = event.get("reason")
                        msg = event.get("message")
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
                msg = event.get("message")
                match = turn_pattern.search(msg) if msg else None
                if match:
                    max_turn = max(max_turn, int(match.group(1)))
        except OSError:
            pass
    if max_turn > 0:
        logger.info("  Turns: %d", max_turn)

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
        for player, actions in player_actions.items():
            logger.info("  %s: %d actions", player, actions)
        logger.info("  Total: $%.4f", total_cost)

    logger.info("=" * 60)
    return total_cost


def print_run_cost_summary(
    sessions: list[GameSession],
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
