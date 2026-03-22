#!/usr/bin/env python3
"""Compatibility wrapper for the canonical annotate-game module."""

# TODO(shim): expires=issue:python-migration-step12 Delete this wrapper once
# callers import magebench.analysis.blunder.annotate_game directly.
import sys

from magebench.analysis.blunder import annotate_game as _impl


def __getattr__(name: str) -> object:
    return getattr(_impl, name)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            f"Usage: {sys.argv[0]} <game.json.gz> <annotations.json>",
            file=sys.stderr,
        )
        sys.exit(1)
    _impl.annotate_game(sys.argv[1], sys.argv[2])
    if "--no-leaderboard" not in sys.argv:
        from magebench.leaderboard.website_data import generate_all_website_data

        generate_all_website_data()
        print("Website data regenerated", file=sys.stderr)
