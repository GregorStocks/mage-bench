"""Migration: v7 -> v8 (make annotation decisionIndex canonical).

Version 7 annotations were identified indirectly by (snapshotIndex, player),
which forced downstream readers to reverse-map them back onto decisions with
heuristics. Version 8 adds decisionIndex as the canonical identity key.

The migration backfills decisionIndex from the existing decisions and snapshots.
snapshotIndex is retained as-is so downgrades can simply drop decisionIndex.
Newly written v8 annotations still emit the canonical aftermath snapshotIndex.
"""

from schemas.game_export_types import require_snapshot
from scripts.analysis.blunder_eval_common import (
    compute_aftermath_index,
    snapshot_index,
)

SOURCE_VERSION = 7
TARGET_VERSION = 8


def _annotation_to_decision_index(data: dict) -> dict[int, int]:
    """Map v7 annotation indices to decision indices using snapshotIndex heuristics.

    V7 annotations only have snapshotIndex, so we reverse-map by computing
    aftermath snapshots and matching (snapshotIndex, player).
    """
    decisions = data.get("decisions", [])
    annotations = data.get("annotations", [])
    raw_snapshots = data.get("snapshots", [])
    assert decisions, "v7 -> v8 migration requires decisions[] to be present"

    snapshots = [
        require_snapshot(s, f"snapshots[{i}]") for i, s in enumerate(raw_snapshots)
    ]
    decision_aftermaths: list[int] = [
        compute_aftermath_index(d, snapshots) for d in decisions
    ]

    result: dict[int, int] = {}
    for ann_idx, ann in enumerate(annotations):
        ann_snap = ann["snapshotIndex"]
        ann_player = ann["player"]

        # Try exact match on aftermath index + player
        best: int | None = None
        for d_idx, d in enumerate(decisions):
            if d["player"] != ann_player:
                continue
            if decision_aftermaths[d_idx] == ann_snap:
                best = d_idx
                break

        # Fallback: closest decision for same player where snapshot_index <= ann_snap
        if best is None:
            best_dist = float("inf")
            for d_idx, d in enumerate(decisions):
                if d["player"] != ann_player:
                    continue
                if snapshot_index(d) <= ann_snap:
                    dist = ann_snap - snapshot_index(d)
                    if dist < best_dist:
                        best_dist = dist
                        best = d_idx

        if best is not None:
            result[ann_idx] = best

    assert len(result) == len(annotations), (
        "v7 -> v8 migration could not map every annotation to a decision: "
        f"{len(result)}/{len(annotations)} mapped"
    )
    return result


def up(data: dict) -> dict:
    """Migrate from v7 to v8: add canonical decisionIndex to annotations."""
    assert data["version"] == 7, f"Expected v7, got v{data['version']}"

    annotations = data.get("annotations", [])
    if annotations:
        mapping = _annotation_to_decision_index(data)
        for ann_idx, ann in enumerate(annotations):
            ann["decisionIndex"] = mapping[ann_idx]

    data["version"] = 8
    return data


def down(data: dict) -> dict:
    """Migrate from v8 to v7: remove annotation decisionIndex."""
    assert data["version"] == 8, f"Expected v8, got v{data['version']}"

    for ann in data.get("annotations", []):
        ann.pop("decisionIndex", None)

    data["version"] = 7
    return data
