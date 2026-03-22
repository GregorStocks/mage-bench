#!/usr/bin/env python3
"""Backfill canonical decisions on existing game exports.

Reads each .json/.json.gz in website/public/games/, builds canonical decisions
from the existing snapshots/actions/llm_events, and writes the file back.

Usage:
    uv run python scripts/backfill_decisions.py [--dry-run] [--force]
"""

import argparse
from pathlib import Path

from magebench.game.export_decisions import build_decisions
from magebench.game.game_exports import (
    GAMES_DIR,
    glob_game_export_paths,
    load_raw_game_export,
    write_raw_game_export,
)


def backfill_game(
    path: Path, *, dry_run: bool = False, force: bool = False
) -> tuple[str, int]:
    """Backfill or rebuild decisions for a single game export."""
    data = load_raw_game_export(path)

    existing = data.get("decisions")
    if existing is not None and not force:
        assert isinstance(existing, list), (
            f"{path.name}: decisions must be a list when present, got {existing!r}"
        )
        return "skipped", len(existing)

    decisions = build_decisions(
        data["snapshots"],
        data["actions"],
        data["llm_events"],
        data.get("harness_epoch", 0),
    )

    if existing == decisions:
        return "unchanged", len(decisions)

    if decisions:
        data["decisions"] = decisions
    elif "decisions" in data:
        del data["decisions"]

    if not dry_run:
        write_raw_game_export(path, data)

    return "updated", len(decisions)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill or rebuild canonical decisions on existing exports."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without writing files."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recompute decisions even when the export already has them.",
    )
    args = parser.parse_args()

    unique_paths = glob_game_export_paths(GAMES_DIR)

    total = 0
    skipped = 0
    unchanged = 0
    updated = 0
    for path in unique_paths:
        status, count = backfill_game(path, dry_run=args.dry_run, force=args.force)
        if status == "skipped":
            skipped += 1
            continue
        if status == "unchanged":
            unchanged += 1
            continue
        updated += 1
        label = "(dry run)" if args.dry_run else ""
        print(f"{path.name}: {count} decisions {label}")
        total += count

    print(
        f"\nDone: {updated} updated, {unchanged} unchanged, {skipped} skipped, {total} total decisions written"
    )


if __name__ == "__main__":
    main()
