#!/usr/bin/env python3
"""Migrate v3 game exports back to v2 (remove cardData and token images).

Reads each .json/.json.gz in website/public/games/, removes cardData,
removes token entries from cardImages, and sets version back to 2.

Skips games already at version 2.

Usage:
    uv run python scripts/migrate_v3_to_v2.py [--dry-run]
"""

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_game import _GZ_THRESHOLD


GAMES_DIR = Path(__file__).resolve().parent.parent / "website" / "public" / "games"


def _is_token_key(name: str) -> bool:
    """Check if a cardImages key is a token (added by v3 migration)."""
    return " Token" in name or " token" in name


def migrate_game(path: Path, *, dry_run: bool = False) -> bool:
    """Migrate a single game export from v3 back to v2. Returns True if migrated."""
    if path.suffix == ".gz":
        raw = gzip.decompress(path.read_bytes())
        data = json.loads(raw)
    else:
        data = json.loads(path.read_text())

    version = data.get("version", 0)
    if version == 2:
        return False
    assert version == 3, f"Unexpected version {version} in {path.name}"

    # Remove cardData
    data.pop("cardData", None)

    # Remove token entries from cardImages
    card_images = data.get("cardImages", {})
    data["cardImages"] = {k: v for k, v in card_images.items() if not _is_token_key(k)}

    data["version"] = 2

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
        if migrate_game(path, dry_run=dry_run):
            label = "(dry run)" if dry_run else ""
            print(f"  {path.name}: migrated to v2 {label}")
            migrated += 1
        else:
            skipped += 1

    print(f"\nDone: {migrated} games migrated, {skipped} skipped (already v2)")


if __name__ == "__main__":
    main()
