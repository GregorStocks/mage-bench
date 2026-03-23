#!/usr/bin/env python3
"""Claim exported games for fast or deep analysis using the local claim store.

Usage:
    claim_games.py --type fast --count 10
    claim_games.py --type deep --count 1
    claim_games.py --type fast game_20260301_010203 game_20260301_020304

Exit codes:
    0  Claimed successfully
    1  No claimable games available / requested game already claimed elsewhere
    2  Bad input
"""

import argparse
import sys

from magebench.analysis.blunder.blunder_eval_common import (
    game_path_for_id,
    validate_game_id,
)
from magebench.common.local_claims import (
    ClaimConflictError,
    claim_exact_keys,
    claim_first_available_keys,
)
from scripts.analysis.find_unanalyzed import find_unanalyzed, game_id_from_path


def _namespace(analysis_type: str) -> str:
    return f"games/{analysis_type}"


def _metadata_for_games(game_ids: list[str], analysis_type: str) -> dict[str, dict[str, str]]:
    return {
        game_id: {
            "game_id": game_id,
            "analysis_type": analysis_type,
        }
        for game_id in game_ids
    }


def _print_claimed_game(game_id: str) -> None:
    print(game_path_for_id(game_id))


def main() -> None:
    parser = argparse.ArgumentParser(description="Claim games for analysis")
    parser.add_argument(
        "--type",
        choices=["fast", "deep"],
        required=True,
        help="Analysis namespace to claim for",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="Number of games to auto-claim when no explicit game IDs are given",
    )
    parser.add_argument(
        "--max-staleness",
        type=int,
        default=30,
        help="Skip games with this many newer analyzed games when auto-claiming",
    )
    parser.add_argument("game_ids", nargs="*")
    args = parser.parse_args()

    if args.game_ids:
        game_ids = [validate_game_id(game_id) for game_id in args.game_ids]
        try:
            claim_exact_keys(
                _namespace(args.type),
                game_ids,
                metadata_by_key=_metadata_for_games(game_ids, args.type),
            )
        except ClaimConflictError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
        for game_id in game_ids:
            _print_claimed_game(game_id)
        return

    candidates = find_unanalyzed(
        args.type,
        None,
        args.max_staleness,
        include_claimed=True,
    )
    candidate_ids = [game_id_from_path(path) for path in candidates]
    claimed = claim_first_available_keys(
        _namespace(args.type),
        candidate_ids,
        args.count,
        metadata_by_key=_metadata_for_games(candidate_ids, args.type),
    )
    if not claimed:
        print("No claimable games available.", file=sys.stderr)
        sys.exit(1)

    for record in claimed:
        _print_claimed_game(record.key)


if __name__ == "__main__":
    main()
