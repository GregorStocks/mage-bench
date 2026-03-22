#!/usr/bin/env python3
"""Compatibility wrapper for the canonical blunder-analysis module."""

# TODO(shim): expires=issue:python-migration-step12 Delete this wrapper once
# callers import magebench.analysis.blunder.blunder_analysis directly.
import sys

from magebench.analysis.blunder import blunder_analysis as _impl


def __getattr__(name: str) -> object:
    return getattr(_impl, name)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            f"Usage: {sys.argv[0]} <game.json.gz | game_id>",
            file=sys.stderr,
        )
        sys.exit(1)
    _impl.main(_impl.resolve_game_path(sys.argv[1]))

    from magebench.leaderboard.website_data import generate_all_website_data

    generate_all_website_data()
    print("Website data regenerated", file=sys.stderr)
