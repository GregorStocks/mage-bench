#!/usr/bin/env python3
"""Fix annotation snapshotIndex off-by-one in existing game exports.

Due to a bug, annotation snapshotIndex was set to the decision snapshot (when
choices were presented) instead of the aftermath snapshot (when the action's
results are visible).  The fix is always +1 because action_seq (the
choose_action gameSeq) equals the decision snapshot's seq, and the resulting
game actions get strictly higher seq values that first appear in the next
snapshot.

Also re-builds decisions to add the actionSeq field for future correctness.

Usage:
    uv run python scripts/backfill_annotation_snapshots.py [--dry-run]
"""

import gzip
import json
import sys
from pathlib import Path

from scripts.export_game import _build_decisions, _GZ_THRESHOLD

GAMES_DIR = Path(__file__).resolve().parent.parent / "website" / "public" / "games"


def _write_game(path: Path, data: dict) -> None:
    """Write a game export, choosing .json or .json.gz based on size."""
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


def backfill_game(path: Path, *, dry_run: bool = False) -> tuple[int, int]:
    """Fix annotation snapshotIndex and re-build decisions.

    Returns (annotations_fixed, decisions_rebuilt).
    """
    if path.suffix == ".gz":
        raw = gzip.decompress(path.read_bytes())
        data = json.loads(raw)
    else:
        data = json.loads(path.read_text())

    snapshots = data.get("snapshots", [])
    annotations = data.get("annotations", [])
    max_idx = len(snapshots) - 1

    # Fix annotation snapshotIndex: advance by 1, clamped to max
    ann_fixed = 0
    for ann in annotations:
        old_idx = ann["snapshotIndex"]
        new_idx = min(old_idx + 1, max_idx)
        if new_idx != old_idx:
            ann["snapshotIndex"] = new_idx
            ann_fixed += 1

    # Re-build decisions to add actionSeq field
    decisions_rebuilt = 0
    if snapshots and data.get("llmEvents"):
        new_decisions = _build_decisions(
            snapshots,
            data.get("actions", []),
            data["llmEvents"],
            data.get("harnessEpoch", 0),
        )
        if new_decisions:
            data["decisions"] = new_decisions
            decisions_rebuilt = len(new_decisions)

    if not dry_run and (ann_fixed > 0 or decisions_rebuilt > 0):
        _write_game(path, data)

    return ann_fixed, decisions_rebuilt


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

    total_ann = 0
    total_dec = 0
    games_modified = 0

    for path in unique_paths:
        ann_fixed, dec_rebuilt = backfill_game(path, dry_run=dry_run)
        if ann_fixed > 0 or dec_rebuilt > 0:
            label = " (dry run)" if dry_run else ""
            parts = []
            if ann_fixed:
                parts.append(f"{ann_fixed} annotations")
            if dec_rebuilt:
                parts.append(f"{dec_rebuilt} decisions rebuilt")
            print(f"  {path.name}: {', '.join(parts)}{label}")
            total_ann += ann_fixed
            total_dec += dec_rebuilt
            games_modified += 1

    print(
        f"\nDone: {games_modified} games modified, "
        f"{total_ann} annotations fixed, "
        f"{total_dec} decisions rebuilt"
    )


if __name__ == "__main__":
    main()
