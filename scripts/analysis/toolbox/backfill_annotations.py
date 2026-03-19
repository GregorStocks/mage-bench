#!/usr/bin/env python3
"""Backfill blunder annotations on games with outdated or missing analysis.

Finds the most recent N games that need (re-)annotation and runs blunder_analysis
on each one sequentially.

Usage:
    uv run --project puppeteer python scripts/analysis/toolbox/backfill_annotations.py [N]
    uv run --project puppeteer python scripts/analysis/toolbox/backfill_annotations.py --game GAME_ID

N defaults to 10. Games are processed most-recent-first.
With --game, re-annotates a specific game regardless of its current version.

Requires OPENROUTER_API_KEY environment variable.
"""

import argparse
from pathlib import Path

from scripts.analysis.blunder_analysis import (
    BLUNDER_SCRIPT_VERSION,
)
from scripts.analysis.blunder_analysis import (
    main as analyze_game,
)
from scripts.analysis.blunder_eval_common import (
    GAMES_DIR,
    game_path_for_id,
    glob_game_files,
    load_game,
)


def find_outdated_games(limit: int) -> list[str]:
    """Find the most recent games needing annotation, newest first."""
    all_games = sorted(glob_game_files(GAMES_DIR), reverse=True)
    outdated: list[str] = []
    for game_path in all_games:
        if len(outdated) >= limit:
            break
        data = load_game(game_path)
        if (
            data.annotations is None
            or data.blunderScriptVersion < BLUNDER_SCRIPT_VERSION
        ):
            outdated.append(str(game_path))
    return outdated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill blunder annotations on outdated games"
    )
    parser.add_argument(
        "limit",
        nargs="?",
        type=int,
        default=10,
        help="Number of games to backfill (default: 10)",
    )
    parser.add_argument(
        "--game",
        help="Re-annotate a specific game ID (forces re-analysis)",
    )
    args = parser.parse_args()

    if args.game:
        gz = str(game_path_for_id(args.game))
        data = load_game(gz)
        current_v = data.blunderScriptVersion if data.annotations is not None else 0
        old_count = len(data.annotations) if data.annotations is not None else 0
        print(f"{args.game}: v{current_v} -> v{BLUNDER_SCRIPT_VERSION}")
        print(f"{'=' * 60}")
        analyze_game(gz)

        data = load_game(gz)
        new_anns = data.annotations
        new_count = len(new_anns) if new_anns is not None else 0
        print(f"  Annotations: {old_count} old -> {new_count} new")
        return

    games = find_outdated_games(args.limit)
    if not games:
        print(f"All games are up to date (v{BLUNDER_SCRIPT_VERSION}).")
        return

    print(
        f"Found {len(games)} game(s) to annotate (target v{BLUNDER_SCRIPT_VERSION}):\n"
    )
    for gz in games:
        game_id = Path(gz).stem.replace(".json", "")
        data = load_game(gz)
        current_v = data.blunderScriptVersion if data.annotations is not None else 0
        print(f"  {game_id}: v{current_v} -> v{BLUNDER_SCRIPT_VERSION}")

    print()
    for i, gz in enumerate(games, 1):
        game_id = Path(gz).stem.replace(".json", "")

        # Count old annotations before analysis
        data = load_game(gz)
        old_count = len(data.annotations) if data.annotations is not None else 0

        print(f"{'=' * 60}")
        print(f"[{i}/{len(games)}] {game_id}")
        print(f"{'=' * 60}")
        analyze_game(gz)

        # Count new annotations after analysis
        data = load_game(gz)
        new_anns = data.annotations
        new_count = len(new_anns) if new_anns is not None else 0
        print(f"  Annotations: {old_count} old -> {new_count} new")
        print()


if __name__ == "__main__":
    main()
