#!/usr/bin/env python3
"""Find game exports that haven't been analyzed yet.

Cross-references game files in website/public/games/ against analysis
files in doc/claudes/analyses/{fast,deep}/.

Skips games that are too stale: if N+ analyses exist for games newer
than a given game, any bugs from that game have likely already been
identified via those newer analyses.

Usage:
    find_unanalyzed.py                    # 10 most recent unanalyzed (fast)
    find_unanalyzed.py --count 5          # 5 most recent
    find_unanalyzed.py --type deep        # check deep/ instead of fast/
    find_unanalyzed.py --max-staleness 50 # skip games with 50+ newer analyses
    find_unanalyzed.py --max-staleness 0  # no staleness limit
"""

import argparse
from pathlib import Path

from blunder_eval_common import GAMES_DIR, REPO_ROOT, glob_game_files

ANALYSES_DIR = REPO_ROOT / "doc" / "claudes" / "analyses"

DEFAULT_MAX_STALENESS = 30


def game_id_from_path(p: Path) -> str:
    """Extract game ID from a file path (strip .json or .json.gz)."""
    name = p.name
    if name.endswith(".json.gz"):
        return name.removesuffix(".json.gz")
    return name.removesuffix(".json")


def find_unanalyzed(analysis_type: str, count: int, max_staleness: int) -> list[Path]:
    analysis_dir = ANALYSES_DIR / analysis_type
    analyzed: set[str] = set()
    if analysis_dir.is_dir():
        for f in analysis_dir.glob("game_*.md"):
            analyzed.add(f.stem)

    all_games = glob_game_files(GAMES_DIR)
    # Reverse so newest first (glob_game_files returns sorted ascending)
    all_games.reverse()

    # Count how many analyses exist for games newer than each candidate.
    # Game IDs are timestamped (game_YYYYMMDD_HHMMSS*) so string sort = chronological.
    # We walk newest-first, tracking how many analyzed games we've passed.
    analyses_seen = 0
    unanalyzed: list[Path] = []
    for game_path in all_games:
        game_id = game_id_from_path(game_path)
        if game_id in analyzed:
            analyses_seen += 1
            continue
        # If too many newer games have already been analyzed, this game
        # is stale — any bugs it contains have likely already been surfaced.
        if max_staleness > 0 and analyses_seen >= max_staleness:
            break
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
    parser.add_argument(
        "--max-staleness",
        type=int,
        default=DEFAULT_MAX_STALENESS,
        help=(
            f"Skip games with this many newer analyzed games (default: {DEFAULT_MAX_STALENESS}). "
            "Set to 0 to disable staleness filtering."
        ),
    )
    args = parser.parse_args()

    games = find_unanalyzed(args.type, args.count, args.max_staleness)
    if not games:
        print("No unanalyzed games found (all games are either analyzed or too stale).")
    else:
        for g in games:
            print(g)


if __name__ == "__main__":
    main()
