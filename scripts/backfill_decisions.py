#!/usr/bin/env python3
"""Backfill canonical decisions on existing game exports.

Reads each .json/.json.gz in website/public/games/, builds canonical decisions
from the existing snapshots/actions/llmEvents, and writes the file back.

Usage:
    uv run python scripts/backfill_decisions.py [--dry-run] [--force]
"""

import argparse
import gzip
import json
from pathlib import Path

from scripts.export_game import _build_decisions
from scripts.migrate_exports import write_game

GAMES_DIR = Path(__file__).resolve().parent.parent / "website" / "public" / "games"


def backfill_game(
    path: Path, *, dry_run: bool = False, force: bool = False
) -> tuple[str, int]:
    """Backfill or rebuild decisions for a single game export."""
    if path.suffix == ".gz":
        raw = gzip.decompress(path.read_bytes())
        data = json.loads(raw)
    else:
        data = json.loads(path.read_text())

    existing = data.get("decisions")
    if existing is not None and not force:
        assert isinstance(existing, list), (
            f"{path.name}: decisions must be a list when present, got {existing!r}"
        )
        return "skipped", len(existing)

    decisions = _build_decisions(
        data["snapshots"],
        data["actions"],
        data["llmEvents"],
        data.get("harnessEpoch", 0),
    )

    if existing == decisions:
        return "unchanged", len(decisions)

    if decisions:
        data["decisions"] = decisions
    elif "decisions" in data:
        del data["decisions"]

    if not dry_run:
        write_game(path, data)

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

    paths = sorted(GAMES_DIR.glob("game_*.json")) + sorted(
        GAMES_DIR.glob("game_*.json.gz")
    )
    # Deduplicate (a game might have both .json and .json.gz)
    seen: set[str] = set()
    unique_paths: list[Path] = []
    for p in paths:
        stem = p.name.replace(".json.gz", "").replace(".json", "")
        if stem not in seen:
            seen.add(stem)
            unique_paths.append(p)

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
