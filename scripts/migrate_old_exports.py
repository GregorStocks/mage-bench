#!/usr/bin/env python3
"""One-time migration: convert v1 (spectator-based) game exports to v2 format.

Processes all game export files in website/public/games/ that lack a top-level
``version`` field. For each file:

- Adds ``version: 2``
- Adds ``gameType``/``deckType``/``llmTrace`` if missing
- Renames ``library_count`` -> ``library_size`` in snapshot players

All other data is preserved (hand contents, hand_count, commanders, etc.).
"""

import gzip
import json
import sys
from pathlib import Path

WEBSITE_GAMES_DIR = (
    Path(__file__).resolve().parent.parent / "website" / "public" / "games"
)


def migrate_export(data: dict) -> bool:
    """Migrate a v1 export dict to v2 format in-place. Returns True if changed."""
    if data.get("version") is not None:
        return False

    data["version"] = 2

    if "gameType" not in data:
        data["gameType"] = ""
    if "deckType" not in data:
        data["deckType"] = ""
    if "llmTrace" not in data:
        data["llmTrace"] = []

    for snap in data.get("snapshots", []):
        for player in snap.get("players", []):
            if "library_count" in player:
                player["library_size"] = player.pop("library_count")

    return True


def migrate_file(path: Path, *, dry_run: bool = False) -> bool:
    """Migrate a single export file. Returns True if the file was changed."""
    if path.name.endswith(".json.gz"):
        data = json.loads(gzip.decompress(path.read_bytes()))
    else:
        data = json.loads(path.read_text())

    if not migrate_export(data):
        return False

    if dry_run:
        return True

    json_str = json.dumps(data, indent=2, ensure_ascii=False)
    json_bytes = json_str.encode()

    # Write back in the same compression format as the original
    if path.name.endswith(".json.gz"):
        path.write_bytes(gzip.compress(json_bytes))
    else:
        path.write_bytes(json_bytes)

    return True


def main() -> None:
    games_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else WEBSITE_GAMES_DIR
    dry_run = "--dry-run" in sys.argv

    paths = sorted(games_dir.glob("game_*.json")) + sorted(
        games_dir.glob("game_*.json.gz")
    )

    migrated = 0
    skipped = 0
    for path in paths:
        if migrate_file(path, dry_run=dry_run):
            migrated += 1
            print(f"  migrated: {path.name}")
        else:
            skipped += 1

    verb = "would migrate" if dry_run else "migrated"
    print(f"\n{verb} {migrated} files, skipped {skipped} (already v2)")


if __name__ == "__main__":
    main()
