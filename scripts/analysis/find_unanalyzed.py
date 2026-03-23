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

from magebench.analysis.blunder.blunder_eval_common import (
    GAMES_DIR,
    REPO_ROOT,
    glob_game_files,
)
from magebench.common.local_claims import list_claims

ANALYSES_DIR = REPO_ROOT / "doc" / "claudes" / "analyses"

DEFAULT_MAX_STALENESS = 30


def game_id_from_path(p: Path) -> str:
    """Extract game ID from a file path (strip .json5/.json, with optional .gz)."""
    name = p.name
    for suffix in (".json5.gz", ".json.gz", ".json5", ".json"):
        if name.endswith(suffix):
            return name.removesuffix(suffix)
    raise AssertionError(f"Unexpected game export path: {p}")


def _claimed_game_ids(analysis_type: str) -> set[str]:
    return {record.key for record in list_claims(f"games/{analysis_type}")}


def find_unanalyzed(
    analysis_type: str,
    count: int | None,
    max_staleness: int,
    *,
    include_claimed: bool = False,
) -> list[Path]:
    analysis_dir = ANALYSES_DIR / analysis_type
    analyzed: set[str] = set()
    if analysis_dir.is_dir():
        for f in analysis_dir.glob("game_*.md"):
            analyzed.add(f.stem)
    claimed = set() if include_claimed else _claimed_game_ids(analysis_type)

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
        if game_id in claimed:
            continue
        # If too many newer games have already been analyzed, this game
        # is stale — any bugs it contains have likely already been surfaced.
        if max_staleness > 0 and analyses_seen >= max_staleness:
            break
        unanalyzed.append(game_path)
        if count is not None and len(unanalyzed) >= count:
            break

    return unanalyzed


def main() -> None:
    parser = argparse.ArgumentParser(description="Find unanalyzed game exports")
    parser.add_argument("--count", type=int, default=10, help="Number of games to list (default: 10)")
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
    parser.add_argument(
        "--include-claimed",
        action="store_true",
        help="Include games currently claimed by another worktree",
    )
    args = parser.parse_args()

    games = find_unanalyzed(
        args.type,
        args.count,
        args.max_staleness,
        include_claimed=args.include_claimed,
    )
    if not games:
        print("No unanalyzed games found (all games are either analyzed or too stale).")
    else:
        for g in games:
            print(g)


if __name__ == "__main__":
    main()
