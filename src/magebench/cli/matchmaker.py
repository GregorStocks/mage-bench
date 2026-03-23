#!/usr/bin/env python3
"""Dry-run round-robin matchmaker to see which models would be paired.

Usage:
  uv run python -m magebench.cli.matchmaker 1v1
  uv run python -m magebench.cli.matchmaker commander
"""

from __future__ import annotations

import argparse

from magebench.leaderboard.matchmaker import get_round_robin_matchup

_MODE_TO_DECK_TYPE = {
    "1v1": "Constructed - Standard",
    "commander": "",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Round-robin matchmaker: fill coverage gaps in the matchup matrix.")
    parser.add_argument(
        "mode",
        choices=["1v1", "commander"],
        help="1v1 (2-player) or commander (4-player)",
    )
    args = parser.parse_args()

    deck_type = _MODE_TO_DECK_TYPE[args.mode]
    num_needed = 2 if args.mode == "1v1" else 4

    picks = get_round_robin_matchup(deck_type, num_needed)
    print(f"  Round-robin selected {len(picks)} presets for {args.mode}:")
    for p in picks:
        print(f"    {p}")


if __name__ == "__main__":
    main()
