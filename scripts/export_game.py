#!/usr/bin/env python3
"""Export a game log directory into a single JSON file for the website visualizer."""

import json
import re
import sys
from pathlib import Path

from magebench.game.game_export_types import BuiltGameExport, require_built_game_export
from puppeteer.harness_epoch import SEASON_1_START_EPOCH
from schemas.game_export_migrations import CURRENT_GAME_EXPORT_VERSION
from scripts.export_card_data import DECKLIST_RE, build_card_data
from scripts.export_decisions import build_decisions
from scripts.export_errors import link_errors_to_decisions, read_errors
from scripts.export_llm_events import read_llm_events
from scripts.game_exports import GAMES_DIR as WEBSITE_GAMES_DIR
from scripts.game_exports import write_raw_game_export
from scripts.generate_leaderboard import generate_all_website_data

_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = Path.home() / ".mage-bench" / "logs"
_TOURNAMENTS_DIR = _ROOT / "data" / "tournaments"

FONT_TAG_RE = re.compile(r"<font[^>]*>|</font>")
OBJECT_ID_RE = re.compile(r"\s*\[[0-9a-f]{3,}\]")
LOST_GAME_RE = re.compile(r"^(.+?) has lost the game\.$")
WON_GAME_RE = re.compile(r"^(.+?) has won the game$")
TIMED_OUT_RE = re.compile(r"^(.+?) has run out of time, losing the match\.$")


class GameExportError(RuntimeError):
    """Operational export failure that callers may treat as non-fatal."""


def _compute_season(harness_epoch: int) -> int:
    """Map historical harness epochs onto the current season numbering."""
    if harness_epoch < SEASON_1_START_EPOCH:
        return 0
    return 1


def _strip_html(message: str) -> str:
    """Remove <font> tags and [hex_id] suffixes from action messages."""
    message = FONT_TAG_RE.sub("", message)
    message = OBJECT_ID_RE.sub("", message)
    return message.strip()


_COMMANDER_DECK_TYPES = {
    "Variant Magic - Freeform Commander",
    "Variant Magic - Commander",
}


def _build_card_images(players_meta: list[dict]) -> dict[str, str]:
    """Build card name -> Scryfall small image URL map from decklists."""
    images = {}
    for player in players_meta:
        decklist = player.get("decklist")
        if decklist is None:
            continue
        for entry in decklist:
            m = DECKLIST_RE.match(entry)
            if not m:
                continue
            set_code = m.group(2).lower()
            card_num = m.group(3)
            card_name = m.group(4).strip()
            images[card_name] = (
                f"https://api.scryfall.com/cards/{set_code}/{card_num}?format=image&version=small"
            )
    return images


def _extract_commander(player_meta: dict) -> str | None:
    """Find commander name from decklist (SB: entries)."""
    decklist = player_meta.get("decklist")
    if decklist is not None:
        for entry in decklist:
            if entry.startswith("SB:"):
                m = DECKLIST_RE.match(entry)
                if m:
                    return m.group(4).strip()
    return None


def _deck_name_from_path(deck_path: str | None) -> str | None:
    """Derive human-readable deck name from file path stem."""
    if not deck_path:
        return None
    return Path(deck_path).stem.replace("-", " ")


def _deck_display_name(player_meta: dict, deck_type: str) -> str | None:
    """Get display name for a player's deck.

    Prefers deck_name from game_meta (set by deck registry resolution).
    Falls back to legacy logic for old game_metas: commander card name
    for commander formats, filename stem for others.
    """
    # New: deck_name from registry
    if player_meta.get("deck_name"):
        deck_name = player_meta["deck_name"]
        assert isinstance(deck_name, str), (
            f"deck_name must be a string, got {deck_name!r}"
        )
        return deck_name
    # Legacy fallback for old game_metas
    if deck_type in _COMMANDER_DECK_TYPES:
        return _extract_commander(player_meta)
    return _deck_name_from_path(player_meta.get("deck_path"))


def read_game_winner(game_dir: Path) -> str | None:
    """Read the winner from the game_end event in server_game_events.jsonl."""
    events_file = game_dir / "server_game_events.jsonl"
    assert events_file.exists(), f"No server_game_events.jsonl in {game_dir}"
    for line in events_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        event = json.loads(line)
        assert isinstance(event, dict), f"{events_file}: expected JSON object per line"
        if event.get("type") == "game_end":
            winner = event.get("winner")
            assert winner is None or isinstance(winner, str), (
                f"{events_file}: game_end winner must be a string or null, got {winner!r}"
            )
            return winner
    return None


def _read_server_events(
    game_dir: Path,
) -> tuple[list[dict], list[dict], dict | None, str | None]:
    """Read events from server_game_events.jsonl.

    Returns (snapshots, actions, game_over_info, winner).
    """
    server_events_path = game_dir / "server_game_events.jsonl"
    assert server_events_path.exists(), f"No server_game_events.jsonl in {game_dir}"

    snapshots: list[dict] = []
    actions: list[dict] = []
    game_over: dict | None = None
    winner: str | None = None

    for line in server_events_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        event = json.loads(line)
        event_type = event.get("type")

        if event_type == "decision" and "state" in event:
            snap = dict(event["state"])
            snap["seq"] = event["seq"]
            snapshots.append(snap)
        elif event_type == "game_action":
            actions.append(
                {
                    "seq": event.get("seq", 0),
                    "message": _strip_html(event["message"]),
                }
            )
        elif event_type == "turn_change":
            actions.append(
                {
                    "seq": event.get("seq", 0),
                    "type": "turn_change",
                    "turn": event["turn"],
                    "active_player": event.get("active_player"),
                }
            )
        elif event_type == "phase_change":
            actions.append(
                {
                    "seq": event.get("seq", 0),
                    "type": "phase_change",
                    "turn": event["turn"],
                    "phase": event.get("phase"),
                    "step": event.get("step"),
                    "active_player": event.get("active_player"),
                }
            )
        elif event_type == "game_end":
            game_over = {
                "seq": event.get("seq", 0),
                "message": event.get("winner") or "Game ended",
            }
            winner = event.get("winner")
            if "state" in event:
                snap = dict(event["state"])
                snap["seq"] = event["seq"]
                snapshots.append(snap)

    return snapshots, actions, game_over, winner


def _find_tournament_for_game(game_id: str) -> str | None:
    """Return tournament identifier (e.g. 'season-1') if game_id is in a bracket."""
    for path in _TOURNAMENTS_DIR.glob("season-*.json"):
        data = json.loads(path.read_text())
        for rnd in data["rounds"]:
            for match in rnd["matches"]:
                for game in match["games"]:
                    if game.get("game_id") == game_id:
                        return path.stem  # e.g. "season-1"
    return None


def build_export(game_dir: Path) -> BuiltGameExport:
    """Build the export data dict from a game directory.

    Reads server_game_events.jsonl (version 2 format).
    """
    meta_path = game_dir / "game_meta.json"

    # Load metadata
    meta: dict = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())

    snapshots, actions, game_over, winner = _read_server_events(game_dir)

    # Read LLM logs
    llm_events, player_costs, player_tools, player_tool_calls, player_thinking = (
        read_llm_events(game_dir)
    )
    # Build card images map from decklists
    card_images = _build_card_images(meta["players"])

    # Build card data (Scryfall metadata) and add token images
    card_images, card_data = build_card_data(card_images, snapshots)

    # Extract game metadata
    game_id = game_dir.name
    total_turns = max((s.get("turn", 0) for s in snapshots), default=0)

    # Fallback for interrupted games where game_end wasn't written
    if not winner:
        for a in actions:
            msg = a.get("message")
            m = WON_GAME_RE.match(msg) if msg else None
            if m:
                winner = m.group(1)
                break

    # Extract placement from elimination order
    player_names = [p.get("name", "?") for p in meta["players"]]
    eliminations = []
    for a in actions:
        msg = a.get("message")
        m = LOST_GAME_RE.match(msg) if msg else None
        if m:
            eliminations.append(m.group(1))
    placements: dict[str, int] = {}
    if winner:
        placements[winner] = 1
        for i, name in enumerate(reversed(eliminations)):
            placements[name] = i + 2
    elif eliminations:
        surviving = [n for n in player_names if n not in eliminations]
        for name in surviving:
            placements[name] = 1
        for i, name in enumerate(reversed(eliminations)):
            placements[name] = len(surviving) + i + 1

    # Derive winner from placements if not already set
    if not winner:
        first_place = [n for n, p in placements.items() if p == 1]
        if len(first_place) == 1:
            winner = first_place[0]

    # Detect timer timeout losses
    timed_out_players: set[str] = set()
    for a in actions:
        msg = a.get("message")
        m = TIMED_OUT_RE.match(msg) if msg else None
        if m:
            timed_out_players.add(m.group(1))

    assert "game_type" in meta, f"{game_id}: game_meta.json missing game_type"
    game_type = meta["game_type"]
    assert isinstance(game_type, str) and game_type, (
        f"{game_id}: expected game_type to be a non-empty string, got {game_type!r}"
    )
    assert "deck_type" in meta, f"{game_id}: game_meta.json missing deck_type"
    deck_type = meta["deck_type"]
    assert isinstance(deck_type, str) and deck_type, (
        f"{game_id}: expected deck_type to be a non-empty string, got {deck_type!r}"
    )
    assert "harness_epoch" in meta, f"{game_id}: game_meta.json missing harness_epoch"
    harness_epoch = meta["harness_epoch"]
    assert isinstance(harness_epoch, int), (
        f"{game_id}: expected harness_epoch to be an int, got {type(harness_epoch).__name__}"
    )

    players_summary = []
    for p in meta["players"]:
        name = p.get("name", "?")
        ok, failed = player_tool_calls.get(name, (0, 0))
        entry: dict = {
            "name": name,
            "type": p.get("type", "?"),
            "deck_name": _deck_display_name(p, deck_type),
            "tool_calls_ok": ok,
            "tool_calls_failed": failed,
            "thinking_time_secs": round(player_thinking.get(name, 0.0), 1),
        }
        if p.get("deck_strategy"):
            entry["deck_strategy"] = p["deck_strategy"]
        if p.get("model"):
            entry["model"] = p["model"]
        if p.get("reasoning_effort"):
            entry["reasoning_effort"] = p["reasoning_effort"]
        if name in player_costs:
            entry["total_cost_usd"] = round(player_costs[name], 4)
        if name in placements:
            entry["placement"] = placements[name]
        if name in player_tools:
            entry["tools"] = player_tools[name]
        if name in timed_out_players:
            entry["timed_out"] = True
        players_summary.append(entry)

    # Build output
    output: dict = {
        "version": CURRENT_GAME_EXPORT_VERSION,
        "id": game_id,
        "timestamp": meta["timestamp"] if "timestamp" in meta else "",
        "game_type": game_type,
        "deck_type": deck_type,
        "total_turns": total_turns,
        "winner": winner,
        "players": players_summary,
        "card_images": card_images,
        "card_data": card_data,
        "snapshots": snapshots,
        "actions": actions,
        "llm_events": llm_events,
        "game_over": game_over,
        "harness_epoch": harness_epoch,
        "youtube_url": meta["youtube_url"] if "youtube_url" in meta else "",
    }

    # Season and tournament fields, added in v4
    if "season" in meta:
        output["season"] = meta["season"]
    else:
        output["season"] = _compute_season(harness_epoch)
    tournament_id: str | None = None
    if meta.get("tournament_game", False):
        tournament_id = _find_tournament_for_game(game_dir.name)
        assert tournament_id is not None, (
            f"tournament_game flag set but {game_dir.name} not found in any bracket"
        )
    else:
        # Check tournament data for older games that predate the meta flag
        tournament_id = _find_tournament_for_game(game_dir.name)
    output["tournament"] = tournament_id
    if tournament_id is not None:
        # Tournament games may not have been annotated yet
        output["annotations"] = []
        output["blunder_script_version"] = 0

    # Build canonical decisions
    decisions = build_decisions(snapshots, actions, llm_events, harness_epoch)
    if decisions:
        output["decisions"] = decisions

    # Read error logs and link to decisions
    errors = read_errors(game_dir)
    if errors:
        if decisions:
            link_errors_to_decisions(errors, decisions, llm_events)
        output["errors"] = errors

    return require_built_game_export(output, source=game_dir.name)


def export_game(game_dir: Path, website_games_dir: Path) -> Path:
    """Export a game directory to a website JSON file. Returns the output path."""
    try:
        output = build_export(game_dir)
        game_id = output.id

        website_games_dir.mkdir(parents=True, exist_ok=True)
        output_path = write_raw_game_export(
            website_games_dir / f"{game_id}.json", output
        )
    except (AssertionError, OSError, json.JSONDecodeError) as exc:
        raise GameExportError(str(exc)) from exc

    return output_path


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <game_id> [website_games_dir]")
        print(f"  game_id: directory name under {LOGS_DIR}")
        sys.exit(1)

    game_id = sys.argv[1]
    game_dir = LOGS_DIR / game_id
    if not game_dir.is_dir():
        print(f"Error: {game_dir} is not a directory")
        sys.exit(1)

    games_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else WEBSITE_GAMES_DIR
    output_path = export_game(game_dir, games_dir)
    size_kb = output_path.stat().st_size // 1024
    print(f"Exported {game_id} -> {output_path} ({size_kb} KB)")

    # Regenerate leaderboard data so committed files stay in sync
    generate_all_website_data(games_dir=games_dir)
    print("Website data regenerated")


if __name__ == "__main__":
    main()
