#!/usr/bin/env python3
"""Dry-run matchmakers to see which models would be paired.

Usage:
  uv run --project puppeteer python scripts/matchmaker.py 1v1
  uv run --project puppeteer python scripts/matchmaker.py commander
  uv run --project puppeteer python scripts/matchmaker.py 1v1 --threshold 1650
  uv run --project puppeteer python scripts/matchmaker.py 1v1 --style round-robin
"""

from __future__ import annotations

import argparse

from puppeteer.matchmaker import get_round_robin_matchup, get_yente_pool

_MODE_TO_DECK_TYPE = {
    "1v1": "Constructed - Standard",
    "commander": "",
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Matchmaker, matchmaker, make me a match!"
    )
    parser.add_argument(
        "mode",
        choices=["1v1", "commander"],
        help="1v1 (Elo ratings) or commander (OpenSkill ratings)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=1600,
        help="Minimum rating to be eligible for yente (default: 1600)",
    )
    parser.add_argument(
        "--style",
        choices=["yente", "round-robin"],
        default="yente",
        help="Matchmaking style: yente (top-rated) or round-robin (coverage gaps)",
    )
    args = parser.parse_args()

    deck_type = _MODE_TO_DECK_TYPE[args.mode]
    num_needed = 2 if args.mode == "1v1" else 4

    if args.style == "yente":
        pool = get_yente_pool(deck_type, threshold=args.threshold)
        if len(pool) < num_needed:
            print(
                f"  Only {len(pool)} eligible, need {num_needed}. Try lowering --threshold."
            )
        else:
            print(f"  {len(pool)} models eligible for {args.mode} yente matchmaking.")
    else:
        picks = get_round_robin_matchup(deck_type, num_needed)
        print(f"  Round-robin selected {len(picks)} presets for {args.mode}:")
        for p in picks:
            print(f"    {p}")


if __name__ == "__main__":
    main()
