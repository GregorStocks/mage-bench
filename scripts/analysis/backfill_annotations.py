#!/usr/bin/env python3
"""Backfill blunder annotations on games with outdated or missing analysis.

Finds the most recent N games that need (re-)annotation and runs blunder_analysis
on each one sequentially.

Usage:
    uv run --project puppeteer python scripts/analysis/backfill_annotations.py [N]

N defaults to 10. Games are processed most-recent-first.

Requires OPENROUTER_API_KEY environment variable.
"""

import glob
import gzip
import json
import sys
from pathlib import Path

from blunder_analysis import BLUNDER_SCRIPT_VERSION, main as analyze_game


def find_outdated_games(limit: int) -> list[str]:
    """Find the most recent games needing annotation, newest first."""
    all_games = sorted(glob.glob("website/public/games/game_*.json.gz"), reverse=True)
    outdated: list[str] = []
    for gz in all_games:
        if len(outdated) >= limit:
            break
        with gzip.open(gz, "rt") as f:
            data = json.load(f)
        if "annotations" not in data:
            outdated.append(gz)
        elif data.get("blunderScriptVersion", 1) < BLUNDER_SCRIPT_VERSION:
            outdated.append(gz)
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
        with gzip.open(gz, "rt") as f:
            data = json.load(f)
        current_v = data.get("blunderScriptVersion", 0) if "annotations" in data else 0
        print(f"  {game_id}: v{current_v} -> v{BLUNDER_SCRIPT_VERSION}")

    print()
    for i, gz in enumerate(games, 1):
        game_id = Path(gz).stem.replace(".json", "")
        print(f"{'=' * 60}")
        print(f"[{i}/{len(games)}] {game_id}")
        print(f"{'=' * 60}")
        analyze_game(gz)
        print()


if __name__ == "__main__":
    main()
