#!/usr/bin/env python3
"""Backfill blunder annotations on games with outdated or missing analysis.

Finds the most recent N games that need (re-)annotation and runs blunder_analysis
on each one sequentially.

Usage:
    uv run --project puppeteer python scripts/analysis/backfill_annotations.py [N]

N defaults to 10. Games are processed most-recent-first.

Requires OPENROUTER_API_KEY environment variable.
"""

import sys
from pathlib import Path

from blunder_analysis import BLUNDER_SCRIPT_VERSION, main as analyze_game
from blunder_eval_common import GAMES_DIR, glob_game_files, load_game


def find_outdated_games(limit: int) -> list[str]:
    """Find the most recent games needing annotation, newest first."""
    all_games = sorted(glob_game_files(GAMES_DIR), reverse=True)
    outdated: list[str] = []
    for game_path in all_games:
        if len(outdated) >= limit:
            break
        data = load_game(game_path)
        if "annotations" not in data:
            outdated.append(str(game_path))
        elif data.get("blunderScriptVersion", 1) < BLUNDER_SCRIPT_VERSION:
            outdated.append(str(game_path))
    return outdated


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10

    games = find_outdated_games(limit)
    if not games:
        print(f"All games are up to date (v{BLUNDER_SCRIPT_VERSION}).")
        return

    print(
        f"Found {len(games)} game(s) to annotate (target v{BLUNDER_SCRIPT_VERSION}):\n"
    )
    for gz in games:
        game_id = Path(gz).stem.replace(".json", "")
        data = load_game(gz)
        current_v = data.get("blunderScriptVersion", 0) if "annotations" in data else 0
        print(f"  {game_id}: v{current_v} -> v{BLUNDER_SCRIPT_VERSION}")

    print()
    for i, gz in enumerate(games, 1):
        game_id = Path(gz).stem.replace(".json", "")

        # Count old annotations before analysis
        data = load_game(gz)
        old_count = len(data["annotations"]) if "annotations" in data else 0

        print(f"{'=' * 60}")
        print(f"[{i}/{len(games)}] {game_id}")
        print(f"{'=' * 60}")
        analyze_game(gz)

        # Count new annotations after analysis
        data = load_game(gz)
        new_count = len(data.get("annotations", []))
        print(f"  Annotations: {old_count} old -> {new_count} new")
        print()


if __name__ == "__main__":
    main()
