#!/usr/bin/env python3
"""Migrate v3 game exports to v4 (add season and tournament fields).

Reads each .json/.json.gz in website/public/games/, computes season from
harnessEpoch, sets tournament to null, and writes the file back at version 4.

Skips games already at version 4 (unless --force).

Usage:
    uv run python scripts/migrate_v3_to_v4.py [--dry-run] [--force]
"""

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "puppeteer" / "src"),
)
from export_game import _GZ_THRESHOLD
from puppeteer.harness_epoch import MIN_LEADERBOARD_EPOCH


GAMES_DIR = Path(__file__).resolve().parent.parent / "website" / "public" / "games"


def compute_season(harness_epoch: int) -> int:
    """Compute season from harness epoch.

    Season 0: pre-season (harnessEpoch < MIN_LEADERBOARD_EPOCH)
    Season 1: everything else
    """
    if harness_epoch < MIN_LEADERBOARD_EPOCH:
        return 0
    return 1


def migrate_game(path: Path, *, dry_run: bool = False, force: bool = False) -> bool:
    """Migrate a single game export from v3 to v4. Returns True if migrated."""
    if path.suffix == ".gz":
        raw = gzip.decompress(path.read_bytes())
        data = json.loads(raw)
    else:
        data = json.loads(path.read_text())

    version = data.get("version", 0)
    if version == 4 and not force:
        return False
    assert version in (3, 4), f"Unexpected version {version} in {path.name}"

    data["season"] = compute_season(data["harnessEpoch"])
    data["tournament"] = None
    data["version"] = 4

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
            print(f"  {path.name}: migrated to v4 {label}")
            migrated += 1
        else:
            skipped += 1

    print(f"\nDone: {migrated} games migrated, {skipped} skipped (already v4)")


if __name__ == "__main__":
    main()
