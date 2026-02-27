#!/usr/bin/env python3
"""Backfill canonical decisions on existing game exports.

Reads each .json/.json.gz in website/public/games/, builds canonical decisions
from the existing snapshots/actions/llmEvents, and writes the file back.

Skips games that already have a 'decisions' field.

Usage:
    uv run python scripts/backfill_decisions.py [--dry-run]
"""

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_game import _build_decisions, _GZ_THRESHOLD


GAMES_DIR = Path(__file__).resolve().parent.parent / "website" / "public" / "games"


def backfill_game(path: Path, *, dry_run: bool = False) -> int:
    """Add decisions to a single game export. Returns decision count."""
    if path.suffix == ".gz":
        raw = gzip.decompress(path.read_bytes())
        data = json.loads(raw)
    else:
        data = json.loads(path.read_text())

    if "decisions" in data:
        return -1  # Already has decisions

    decisions = _build_decisions(
        data.get("snapshots", []),
        data.get("actions", []),
        data.get("llmEvents", []),
        data.get("harnessEpoch", 0),
    )

    if not decisions:
        return 0

    data["decisions"] = decisions

    if not dry_run:
        json_str = json.dumps(data, indent=2, ensure_ascii=False)
        json_bytes = json_str.encode()

        if len(json_bytes) > _GZ_THRESHOLD:
            out_path = path.with_suffix("") if path.suffix == ".gz" else path
            out_path = out_path.with_suffix(".json.gz")
            out_path.write_bytes(gzip.compress(json_bytes))
            # Clean up alternate format
            alt = (
                out_path.with_suffix(".json")
                if out_path.suffix == ".gz"
                else out_path.with_suffix(".json.gz")
            )
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

    return len(decisions)


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

    total = 0
    skipped = 0
    for path in unique_paths:
        count = backfill_game(path, dry_run=dry_run)
        if count == -1:
            skipped += 1
            continue
        label = "(dry run)" if dry_run else ""
        print(f"{path.name}: {count} decisions {label}")
        total += count

    print(
        f"\nDone: {len(unique_paths) - skipped} games processed, {skipped} skipped (already had decisions), {total} total decisions"
    )


if __name__ == "__main__":
    main()
