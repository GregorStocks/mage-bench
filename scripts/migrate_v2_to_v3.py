#!/usr/bin/env python3
"""Migrate v2 game exports to v3 (add cardData and token images).

Reads each .json/.json.gz in website/public/games/, builds cardData from
snapshots using Scryfall, and writes the file back at version 3.

Skips games already at version 3 (unless --force).

Usage:
    uv run python scripts/migrate_v2_to_v3.py [--dry-run] [--force]
"""

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_game import _build_card_data, _GZ_THRESHOLD


GAMES_DIR = Path(__file__).resolve().parent.parent / "website" / "public" / "games"


def migrate_game(path: Path, *, dry_run: bool = False, force: bool = False) -> bool:
    """Migrate a single game export from v2 to v3. Returns True if migrated."""
    if path.suffix == ".gz":
        raw = gzip.decompress(path.read_bytes())
        data = json.loads(raw)
    else:
        data = json.loads(path.read_text())

    version = data.get("version", 0)
    if version == 3 and not force:
        return False
    assert version in (2, 3), f"Unexpected version {version} in {path.name}"

    card_images, card_data = _build_card_data(
        data.get("cardImages", {}),
        data.get("snapshots", []),
    )

    data["cardImages"] = card_images
    data["cardData"] = card_data
    data["version"] = 3

    if not dry_run:
        json_str = json.dumps(data, indent=2, ensure_ascii=False)
        json_bytes = json_str.encode()

        if len(json_bytes) > _GZ_THRESHOLD:
            out_path = path.with_suffix("") if path.suffix == ".gz" else path
            out_path = out_path.with_suffix(".json.gz")
            out_path.write_bytes(gzip.compress(json_bytes))
            alt = out_path.with_suffix(".json")
            if alt != path and alt.exists():
                alt.unlink()
            if path != out_path and path.exists():
                path.unlink()
        else:
            out_path = path.with_suffix("") if path.suffix == ".gz" else path
            out_path = out_path.with_suffix(".json")
            out_path.write_bytes(json_bytes)
            alt = out_path.with_suffix(".json.gz")
            if alt != path and alt.exists():
                alt.unlink()
            if path != out_path and path.exists():
                path.unlink()

    return True


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv

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

    migrated = 0
    skipped = 0
    for path in unique_paths:
        if migrate_game(path, dry_run=dry_run, force=force):
            label = "(dry run)" if dry_run else ""
            print(f"  {path.name}: migrated to v3 {label}")
            migrated += 1
        else:
            skipped += 1

    print(f"\nDone: {migrated} games migrated, {skipped} skipped (already v3)")


if __name__ == "__main__":
    main()
