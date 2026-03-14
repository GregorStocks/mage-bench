#!/usr/bin/env python3
"""Migrate tournament draft histories to a target version.

Usage:
    uv run python scripts/migrate_draft_histories.py --to 2 [--dry-run] [--force]
"""

import argparse
import json
from pathlib import Path

from scripts.draft_history import (
    CURRENT_DRAFT_HISTORY_VERSION,
    iter_tournament_paths,
    migrate_draft_history_to_current,
)


def migrate_tournament(
    path: Path, target_version: int, dry_run: bool, force: bool
) -> bool:
    """Migrate one tournament file if needed. Returns True if it changed."""
    tournament = json.loads(path.read_text())
    draft = tournament.get("draft")
    if draft is None:
        return False

    current_version = draft.get("history_version", 1)
    assert target_version == CURRENT_DRAFT_HISTORY_VERSION, (
        f"Unsupported target draft history version: v{target_version}"
    )
    if current_version == target_version and not force:
        return False

    migrate_draft_history_to_current(draft)
    if not dry_run:
        path.write_text(json.dumps(tournament, indent=2) + "\n")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate tournament draft histories to a target version."
    )
    parser.add_argument(
        "--to",
        type=int,
        required=True,
        dest="target_version",
        help="Target draft history version",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing files",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rewrite files already at the target version",
    )
    args = parser.parse_args()

    migrated = 0
    skipped = 0
    for path in iter_tournament_paths():
        if migrate_tournament(path, args.target_version, args.dry_run, args.force):
            action = "would migrate" if args.dry_run else "migrated"
            print(f"  {path.name}: {action} to v{args.target_version}")
            migrated += 1
        else:
            skipped += 1

    print(
        f"\nDone: {migrated} migrated, {skipped} skipped "
        f"(target v{args.target_version})"
    )


if __name__ == "__main__":
    main()
