#!/usr/bin/env python3
"""Dry-run the Yente matchmaker to see which models would be paired.

Usage:
  uv run --project puppeteer python scripts/matchmaker.py 1v1
  uv run --project puppeteer python scripts/matchmaker.py commander
  uv run --project puppeteer python scripts/matchmaker.py 1v1 --threshold 1650
"""

from __future__ import annotations

import argparse

from puppeteer.matchmaker import get_yente_pool

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
        help="Minimum rating to be eligible (default: 1600)",
    )
    args = parser.parse_args()

    deck_type = _MODE_TO_DECK_TYPE[args.mode]
    pool = get_yente_pool(deck_type, threshold=args.threshold)
    num_needed = 2 if args.mode == "1v1" else 4
    if len(pool) < num_needed:
        print(
            f"  Only {len(pool)} eligible, need {num_needed}. Try lowering --threshold."
        )
    else:
        print(f"  {len(pool)} models eligible for {args.mode} yente matchmaking.")


if __name__ == "__main__":
    main()
