"""Migration: v7 -> v8 (make annotation decisionIndex canonical).

Version 7 annotations were identified indirectly by (snapshotIndex, player),
which forced downstream readers to reverse-map them back onto decisions with
heuristics. Version 8 adds decisionIndex as the canonical identity key.

The migration backfills decisionIndex from the existing decisions and snapshots.
snapshotIndex is retained as-is so downgrades can simply drop decisionIndex.
Newly written v8 annotations still emit the canonical aftermath snapshotIndex.
"""

SOURCE_VERSION = 7
TARGET_VERSION = 8


def _annotation_to_decision_index(data: dict) -> dict[int, int]:
    from scripts.analysis.blunder_eval_common import reverse_map_annotations

    decisions = data.get("decisions", [])
    annotations = data.get("annotations", [])
    snapshots = data.get("snapshots", [])
    assert decisions, "v7 -> v8 migration requires decisions[] to be present"
    mapping = reverse_map_annotations(annotations, decisions, snapshots)
    assert len(mapping) == len(annotations), (
        "v7 -> v8 migration could not map every annotation to a decision: "
        f"{len(mapping)}/{len(annotations)} mapped"
    )
    return mapping


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
