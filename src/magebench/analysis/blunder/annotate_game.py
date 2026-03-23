#!/usr/bin/env python3
"""Patch a game export with blunder annotations.

Reads the export file, adds or replaces the top-level 'annotations' array,
validates the annotation schema, and writes back.
"""

import json
import sys

from magebench.game.game_exports import load_raw_game_export, write_raw_game_export

VALID_SEVERITIES = {"questionable", "minor", "moderate", "major"}
REQUIRED_FIELDS = {
    "decision_index",
    "player",
    "type",
    "severity",
    "description",
    "action_taken",
    "better_line",
}


def _validate_annotation(ann: dict, index: int, game_data: dict) -> None:
    """Validate a single annotation. Crashes on invalid input."""
    missing = REQUIRED_FIELDS - set(ann.keys())
    assert not missing, f"Annotation {index}: missing fields: {missing}"

    assert isinstance(ann["decision_index"], int), (
        f"Annotation {index}: decision_index must be int, got {type(ann['decision_index']).__name__}"
    )

    if "snapshot_index" in ann:
        assert isinstance(ann["snapshot_index"], int), (
            f"Annotation {index}: snapshot_index must be int, got {type(ann['snapshot_index']).__name__}"
        )
        num_snapshots = len(game_data["snapshots"])
        assert 0 <= ann["snapshot_index"] < num_snapshots, (
            f"Annotation {index}: snapshot_index {ann['snapshot_index']} out of range [0, {num_snapshots})"
        )

    decisions = game_data.get("decisions")
    assert isinstance(decisions, list) and decisions, (
        f"Annotation {index}: decision_index validation requires non-empty decisions[]"
    )
    assert 0 <= ann["decision_index"] < len(decisions), (
        f"Annotation {index}: decision_index {ann['decision_index']} out of range [0, {len(decisions)})"
    )

    player_names = {p["name"] for p in game_data["players"]}
    assert ann["player"] in player_names, (
        f"Annotation {index}: player '{ann['player']}' not in game players {player_names}"
    )
    decision_player = decisions[ann["decision_index"]].get("player")
    assert ann["player"] == decision_player, (
        f"Annotation {index}: player '{ann['player']}' does not match "
        f"decision_index {ann['decision_index']} player '{decision_player}'"
    )

    assert ann["type"] == "blunder", f"Annotation {index}: type must be 'blunder', got '{ann['type']}'"

    assert ann["severity"] in VALID_SEVERITIES, (
        f"Annotation {index}: severity '{ann['severity']}' not in {VALID_SEVERITIES}"
    )


def annotate_game(
    gz_path: str,
    annotations_path: str,
    *,
    blunder_script_version: int | None = None,
) -> None:
    """Patch a game export file with annotations. Handles both .json and .json.gz."""
    game_data = load_raw_game_export(gz_path)

    with open(annotations_path) as f:
        annotations = json.load(f)

    assert isinstance(annotations, list), f"Annotations must be a JSON array, got {type(annotations).__name__}"

    for i, ann in enumerate(annotations):
        _validate_annotation(ann, i, game_data)

    game_data["annotations"] = annotations
    if blunder_script_version is not None:
        game_data["blunder_script_version"] = blunder_script_version

    write_raw_game_export(gz_path, game_data, compress=gz_path.endswith(".json.gz"))

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
