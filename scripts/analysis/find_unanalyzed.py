#!/usr/bin/env python3
"""Find game exports that haven't been analyzed yet.

Cross-references game files in website/public/games/ against analysis
files in doc/claudes/analyses/{fast,deep}/.

Usage:
    find_unanalyzed.py                # 10 most recent unanalyzed (fast)
    find_unanalyzed.py --count 5      # 5 most recent
    find_unanalyzed.py --type deep    # check deep/ instead of fast/
"""

import argparse
from pathlib import Path

from blunder_eval_common import GAMES_DIR, REPO_ROOT, glob_game_files

ANALYSES_DIR = REPO_ROOT / "doc" / "claudes" / "analyses"


def game_id_from_path(p: Path) -> str:
    """Extract game ID from a file path (strip .json or .json.gz)."""
    name = p.name
    if name.endswith(".json.gz"):
        return name.removesuffix(".json.gz")
    return name.removesuffix(".json")


def find_unanalyzed(analysis_type: str, count: int) -> list[Path]:
    analysis_dir = ANALYSES_DIR / analysis_type
    analyzed: set[str] = set()
    if analysis_dir.is_dir():
        for f in analysis_dir.glob("game_*.md"):
            analyzed.add(f.stem)

    all_games = glob_game_files(GAMES_DIR)
    # Reverse so newest first (glob_game_files returns sorted ascending)
    all_games.reverse()

    unanalyzed: list[Path] = []
    for game_path in all_games:
        game_id = game_id_from_path(game_path)
        if game_id not in analyzed:
            unanalyzed.append(game_path)
            if len(unanalyzed) >= count:
                break

    return unanalyzed


def main() -> None:
    parser = argparse.ArgumentParser(description="Find unanalyzed game exports")
    parser.add_argument(
        "--count", type=int, default=10, help="Number of games to list (default: 10)"
    )
    parser.add_argument(
        "--type",
        choices=["fast", "deep"],
        default="fast",
        help="Analysis type to check (default: fast)",
    )
    args = parser.parse_args()

    games = find_unanalyzed(args.type, args.count)
    for g in games:
        print(g)


if __name__ == "__main__":
    main()
