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
    "decisionIndex",
    "player",
    "type",
    "severity",
    "description",
    "actionTaken",
    "betterLine",
}


def _validate_annotation(ann: dict, index: int, game_data: dict) -> None:
    """Validate a single annotation. Crashes on invalid input."""
    missing = REQUIRED_FIELDS - set(ann.keys())
    assert not missing, f"Annotation {index}: missing fields: {missing}"

    assert isinstance(ann["decisionIndex"], int), (
        f"Annotation {index}: decisionIndex must be int, got {type(ann['decisionIndex']).__name__}"
    )

    if "snapshotIndex" in ann:
        assert isinstance(ann["snapshotIndex"], int), (
            f"Annotation {index}: snapshotIndex must be int, got {type(ann['snapshotIndex']).__name__}"
        )
        num_snapshots = len(game_data["snapshots"])
        assert 0 <= ann["snapshotIndex"] < num_snapshots, (
            f"Annotation {index}: snapshotIndex {ann['snapshotIndex']} out of range [0, {num_snapshots})"
        )

    decisions = game_data.get("decisions")
    assert isinstance(decisions, list) and decisions, (
        f"Annotation {index}: decisionIndex validation requires non-empty decisions[]"
    )
    assert 0 <= ann["decisionIndex"] < len(decisions), (
        f"Annotation {index}: decisionIndex {ann['decisionIndex']} out of range [0, {len(decisions)})"
    )

    player_names = {p["name"] for p in game_data["players"]}
    assert ann["player"] in player_names, (
        f"Annotation {index}: player '{ann['player']}' not in game players {player_names}"
    )
    decision_player = decisions[ann["decisionIndex"]].get("player")
    assert ann["player"] == decision_player, (
        f"Annotation {index}: player '{ann['player']}' does not match "
        f"decisionIndex {ann['decisionIndex']} player '{decision_player}'"
    )

    assert ann["type"] == "blunder", (
        f"Annotation {index}: type must be 'blunder', got '{ann['type']}'"
    )

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
    is_gz = gz_path.endswith(".json.gz")
    if is_gz:
        with gzip.open(gz_path, "rt") as f:
            game_data = json.load(f)
    else:
        with open(gz_path) as f:
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

    if is_gz:
        with gzip.open(gz_path, "wt") as f:
            json.dump(game_data, f, indent=2, ensure_ascii=False)
    else:
        with open(gz_path, "w") as f:
            json.dump(game_data, f, indent=2, ensure_ascii=False)

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
    if "--no-leaderboard" not in sys.argv:
        from scripts.generate_leaderboard import generate_all_website_data

        generate_all_website_data()
        print("Website data regenerated", file=sys.stderr)
