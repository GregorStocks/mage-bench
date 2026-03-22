#!/usr/bin/env python3
"""Fix annotation snapshot_index off-by-one in existing game exports.

Due to a bug, annotation snapshot_index was set to the decision snapshot (when
choices were presented) instead of the aftermath snapshot (when the action's
results are visible).  The fix is always +1 because action_seq (the
choose_action game_seq) equals the decision snapshot's seq, and the resulting
game actions get strictly higher seq values that first appear in the next
snapshot.

Also re-builds decisions to add the action_seq field for future correctness.

Usage:
    uv run python scripts/backfill_annotation_snapshots.py [--dry-run]
"""

import sys
from pathlib import Path

from scripts.export_decisions import build_decisions
from scripts.game_exports import (
    GAMES_DIR,
    glob_game_export_paths,
    load_raw_game_export,
    write_raw_game_export,
)


def backfill_game(path: Path, *, dry_run: bool = False) -> tuple[int, int]:
    """Fix annotation snapshot_index and re-build decisions.

    Returns (annotations_fixed, decisions_rebuilt).
    """
    data = load_raw_game_export(path)

    snapshots = data["snapshots"]
    annotations = data.get("annotations")
    if annotations is None:
        annotations = []
    max_idx = len(snapshots) - 1

    # Fix annotation snapshot_index: advance by 1, clamped to max
    ann_fixed = 0
    for ann in annotations:
        old_idx = ann["snapshot_index"]
        new_idx = min(old_idx + 1, max_idx)
        if new_idx != old_idx:
            ann["snapshot_index"] = new_idx
            ann_fixed += 1

    # Re-build decisions to add action_seq field
    decisions_rebuilt = 0
    if snapshots and data.get("llm_events"):
        new_decisions = build_decisions(
            snapshots,
            data["actions"],
            data["llm_events"],
            data.get("harness_epoch", 0),
        )
        if new_decisions:
            data["decisions"] = new_decisions
            decisions_rebuilt = len(new_decisions)

    if not dry_run and (ann_fixed > 0 or decisions_rebuilt > 0):
        write_raw_game_export(path, data)

    return ann_fixed, decisions_rebuilt


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    unique_paths = glob_game_export_paths(GAMES_DIR)

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
        f"\nDone: {games_modified} games modified, {total_ann} annotations fixed, {total_dec} decisions rebuilt"
    )


if __name__ == "__main__":
    main()
