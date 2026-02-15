#!/usr/bin/env python3
"""Patch a .json.gz game export with blunder annotations.

Reads the gz file, adds or replaces the top-level 'annotations' array,
validates the annotation schema, and writes back.
"""

import gzip
import json
import sys

VALID_SEVERITIES = {"questionable", "minor", "moderate", "major"}
REQUIRED_FIELDS = {
    "snapshotIndex",
    "player",
    "type",
    "severity",
    "category",
    "description",
    "llmReasoning",
    "actionTaken",
    "betterLine",
}


def _validate_annotation(ann: dict, index: int, game_data: dict) -> None:
    """Validate a single annotation. Crashes on invalid input."""
    missing = REQUIRED_FIELDS - set(ann.keys())
    assert not missing, f"Annotation {index}: missing fields: {missing}"

    assert isinstance(ann["snapshotIndex"], int), (
        f"Annotation {index}: snapshotIndex must be int, got {type(ann['snapshotIndex']).__name__}"
    )

    num_snapshots = len(game_data.get("snapshots", []))
    assert 0 <= ann["snapshotIndex"] < num_snapshots, (
        f"Annotation {index}: snapshotIndex {ann['snapshotIndex']} out of range [0, {num_snapshots})"
    )

    player_names = {p["name"] for p in game_data.get("players", [])}
    assert ann["player"] in player_names, (
        f"Annotation {index}: player '{ann['player']}' not in game players {player_names}"
    )

    assert ann["type"] == "blunder", (
        f"Annotation {index}: type must be 'blunder', got '{ann['type']}'"
    )

    assert ann["severity"] in VALID_SEVERITIES, (
        f"Annotation {index}: severity '{ann['severity']}' not in {VALID_SEVERITIES}"
    )

    assert isinstance(ann["category"], str) and ann["category"], (
        f"Annotation {index}: category must be a non-empty string"
    )


def annotate_game(
    gz_path: str,
    annotations_path: str,
    *,
    blunder_script_version: int | None = None,
) -> None:
    """Patch a gz file with annotations."""
    with gzip.open(gz_path, "rt") as f:
        game_data = json.load(f)

    with open(annotations_path) as f:
        annotations = json.load(f)

    assert isinstance(annotations, list), (
        f"Annotations must be a JSON array, got {type(annotations).__name__}"
    )

    for i, ann in enumerate(annotations):
        _validate_annotation(ann, i, game_data)

    game_data["annotations"] = annotations
    if blunder_script_version is not None:
        game_data["blunderScriptVersion"] = blunder_script_version

    with gzip.open(gz_path, "wt") as f:
        json.dump(game_data, f)

    print(
        f"Wrote {len(annotations)} annotation(s) to {gz_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            f"Usage: {sys.argv[0]} <game.json.gz> <annotations.json>",
            file=sys.stderr,
        )
        sys.exit(1)
    annotate_game(sys.argv[1], sys.argv[2])
