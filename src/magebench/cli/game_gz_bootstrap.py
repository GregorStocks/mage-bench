#!/usr/bin/env python3
"""Bootstrap game analysis: export gz if needed, print quick overview.

Usage:
    game_gz_bootstrap.py <game_id>

Looks for website/public/games/<game_id>.json.gz or .json. If neither exists
but raw logs are available at ~/.mage-bench/logs/<game_id>/game_events.jsonl,
runs the export-game CLI to generate the export first.
"""

import json
import subprocess
import sys
from pathlib import Path

from magebench.analysis.blunder.blunder_eval_common import GAMES_DIR, load_game
from magebench.game.export_game import LOGS_DIR
from magebench.game.game_export_types import LlmEvent, ToolCallEvent

_EXTENSIONS = (".json.gz", ".json")


def _find_export(game_id: str) -> Path | None:
    """Return the export path (.json.gz or .json), or None."""
    for ext in _EXTENSIONS:
        p = GAMES_DIR / f"{game_id}{ext}"
        if p.exists():
            return p
    return None


def _parse_tool_result(event: ToolCallEvent) -> dict | None:
    """Parse a tool_call result payload if it is structured JSON."""
    result = event.result
    if not result:
        return None
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_failed_tool_call(event: LlmEvent) -> bool:
    """Count only explicit tool error payloads, not arbitrary substrings."""
    if event.type != "tool_call":
        return False
    assert isinstance(event, ToolCallEvent)
    result = _parse_tool_result(event)
    if result is None:
        return False
    if result.get("success") is False:
        return True
    if result.get("success") is True:
        return False
    error = result.get("error")
    if isinstance(error, str):
        return bool(error.strip())
    return error is not None


def _failed_tool_calls(events: list[LlmEvent]) -> list[ToolCallEvent]:
    """Return tool_call events with explicit structured failures."""
    return [event for event in events if isinstance(event, ToolCallEvent) and _is_failed_tool_call(event)]


def _format_result_preview(event: ToolCallEvent) -> str:
    """Render a short preview of the raw result payload."""
    return event.result[:120]


def main(game_id: str) -> None:
    game_dir = LOGS_DIR / game_id
    events_path = game_dir / "game_events.jsonl"

    export_path = _find_export(game_id)
    if export_path is None and events_path.exists():
        subprocess.run(
            ["uv", "run", "python", "-m", "magebench.cli.export_game", game_id],
            check=True,
        )
        # Export may create .json.gz or .json depending on file size
        export_path = _find_export(game_id)

    if export_path is None:
        print(f"No export found for {game_id}", file=sys.stderr)
        for ext in _EXTENSIONS:
            print(f"  Checked: {GAMES_DIR / f'{game_id}{ext}'}", file=sys.stderr)
        print(
            f"  Raw logs: {'exist' if events_path.exists() else 'not found'}",
            file=sys.stderr,
        )
        sys.exit(1)

    d = load_game(export_path)

    print(f"Game: {d.id} | {d.deck_type} | {d.total_turns} turns | Winner: {d.winner}")
    for p in d.players:
        cost = p.total_cost_usd or 0
        print(f"  {p.name} ({p.model or '?'}) ${cost:.2f}")

    events = d.llm_events
    errors = _failed_tool_calls(events)
    print(f"LLM events: {len(events)} | Failed tool calls: {len(errors)}")
    for e in errors[:5]:
        print(f"  {e.player} | {e.tool} | {_format_result_preview(e)}")
    if len(errors) > 5:
        print(f"  ... and {len(errors) - 5} more")


if __name__ == "__main__":
    assert len(sys.argv) == 2, f"Usage: {sys.argv[0]} <game_id>"
    main(sys.argv[1])
