#!/usr/bin/env python3
"""Bootstrap game analysis: export gz if needed, print quick overview.

Usage:
    game-gz-bootstrap.py <game_id>

Looks for website/public/games/<game_id>.json.gz or .json. If neither exists
but raw logs are available at ~/mage-bench-logs/<game_id>/game_events.jsonl,
runs export_game.py to generate the export first.
"""

import subprocess
import sys
from pathlib import Path

from analysis.blunder_eval_common import GAMES_DIR, load_game

LOGS_DIR = Path.home() / "mage-bench-logs"

_EXTENSIONS = (".json.gz", ".json")


def _find_export(game_id: str) -> Path | None:
    """Return the export path (.json.gz or .json), or None."""
    for ext in _EXTENSIONS:
        p = GAMES_DIR / f"{game_id}{ext}"
        if p.exists():
            return p
    return None


def main(game_id: str) -> None:
    game_dir = LOGS_DIR / game_id
    events_path = game_dir / "game_events.jsonl"

    export_path = _find_export(game_id)
    if export_path is None and events_path.exists():
        subprocess.run(
            ["uv", "run", "python", "scripts/export_game.py", game_id],
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

    print(
        f"Game: {d['id']} | {d.get('deckType', '?')} | {d['totalTurns']} turns | Winner: {d['winner']}"
    )
    for p in d["players"]:
        cost = p.get("totalCostUsd", 0)
        print(f"  {p['name']} ({p.get('model', '?')}) ${cost:.2f}")

    events = d.get("llmEvents", [])
    errors = [
        e
        for e in events
        if e.get("type") == "tool_call"
        and any(
            x in str(e.get("result", "")).lower()
            for x in ["error", "out of range", "required", "failed"]
        )
    ]
    print(f"LLM events: {len(events)} | Failed tool calls: {len(errors)}")
    for e in errors[:5]:
        result_str = str(e.get("result", ""))[:120]
        print(f"  {e.get('player', '?')} | {e.get('tool', '?')} | {result_str}")
    if len(errors) > 5:
        print(f"  ... and {len(errors) - 5} more")


if __name__ == "__main__":
    assert len(sys.argv) == 2, f"Usage: {sys.argv[0]} <game_id>"
    main(sys.argv[1])
