#!/usr/bin/env python3
"""Unified migration runner for game export format versions.

Migrates .json/.json.gz game exports between versions by chaining
migration modules from schemas/migrations/.

Usage:
    uv run python scripts/migrate_exports.py --to VERSION [--dry-run] [--force]

Examples:
    uv run python scripts/migrate_exports.py --to 4              # Migrate all games to v4
    uv run python scripts/migrate_exports.py --to 3 --dry-run    # Preview downgrade to v3
    uv run python scripts/migrate_exports.py --to 5 --force      # Re-run even if already at v5
"""

import argparse
from pathlib import Path
from types import ModuleType

from schemas.migrations import MIGRATIONS
from scripts.game_exports import (
    GAMES_DIR,
    glob_game_export_paths,
    load_raw_game_export,
    write_raw_game_export,
)


def find_migration_path(
    current_version: int,
    target_version: int,
    migrations: list[ModuleType],
) -> list[tuple[ModuleType, str]]:
    """Return ordered list of (module, direction) tuples to reach target_version.

    Raises AssertionError if no path exists.
    """
    if current_version == target_version:
        return []

    if current_version < target_version:
        path = []
        v = current_version
        for m in migrations:
            if v == m.SOURCE_VERSION:
                path.append((m, "up"))
                v = m.TARGET_VERSION
                if v == target_version:
                    return path
        raise AssertionError(
            f"No migration path from v{current_version} to v{target_version}"
        )
    path = []
    v = current_version
    for m in reversed(migrations):
        if v == m.TARGET_VERSION:
            path.append((m, "down"))
            v = m.SOURCE_VERSION
            if v == target_version:
                return path
    raise AssertionError(
        f"No migration path from v{current_version} to v{target_version}"
    )


def migrate_game(
    path: Path,
    target_version: int,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> bool:
    """Migrate a single game export to the target version. Returns True if migrated."""
    data = load_raw_game_export(path)
    current_version = data["version"]

    if current_version == target_version and not force:
        return False

    migration_path = find_migration_path(current_version, target_version, MIGRATIONS)
    assert migration_path, (
        f"Game {path.name} is at v{current_version}, target is v{target_version}, "
        f"but --force was used with no migration steps"
    )

    for module, direction in migration_path:
        func = module.up if direction == "up" else module.down
        data = func(data)

    assert data["version"] == target_version, (
        f"Migration chain ended at v{data['version']}, expected v{target_version}"
    )

    if not dry_run:
        write_raw_game_export(path, data)

    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate game exports between format versions.",
    )
    parser.add_argument(
        "--to",
        type=int,
        required=True,
        dest="target_version",
        help="Target export format version.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without writing."
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-run even if already at target version."
    )
    args = parser.parse_args()

    paths = glob_game_export_paths(GAMES_DIR)
    migrated = 0
    skipped = 0

    for path in paths:
        if migrate_game(
            path, args.target_version, dry_run=args.dry_run, force=args.force
        ):
            label = "(dry run)" if args.dry_run else ""
            print(f"  {path.name}: migrated to v{args.target_version} {label}")
            migrated += 1
        else:
            skipped += 1

    print(
        f"\nDone: {migrated} games migrated, {skipped} skipped (already v{args.target_version})"
    )


if __name__ == "__main__":
    main()
