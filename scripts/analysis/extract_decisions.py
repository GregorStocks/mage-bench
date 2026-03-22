#!/usr/bin/env python3
"""Compatibility wrapper for the canonical extract-decisions module."""

# TODO(shim): expires=issue:python-migration-step12 Delete this wrapper once
# callers import magebench.analysis.blunder.extract_decisions directly.
import sys

from magebench.analysis.blunder import extract_decisions as _impl


def __getattr__(name: str) -> object:
    return getattr(_impl, name)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <game.json.gz>", file=sys.stderr)
        sys.exit(1)
    _impl.main(sys.argv[1])
