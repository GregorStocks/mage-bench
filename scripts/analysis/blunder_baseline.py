#!/usr/bin/env python3
"""Derive blunder baseline from current game file annotations.

For each validated play in the ground truth, checks whether the current
game file contains an annotation at the matching snapshot index + player.

No LLM calls -- reads directly from game files.

Usage:
    uv run --project puppeteer python scripts/analysis/blunder_baseline.py
"""

import gzip
import json
import sys
from datetime import datetime, timezone

from blunder_analysis import BLUNDER_SCRIPT_VERSION
from blunder_eval_common import (
    compute_aftermath_index,
    game_path_for_id,
    load_ground_truth,
    play_key,
    save_baseline,
)
from extract_decisions import extract_decisions


def derive_baseline() -> dict:
    """Derive baseline results from current game annotations.

    For each validated (non-null verdict) entry across all games,
    checks whether the game file has an annotation at the play's
    aftermath_index + player.
    """
    all_gt = load_ground_truth()
    results: dict[str, dict] = {}

    # Collect validated entries grouped by game
    games_with_validated: dict[str, list[dict]] = {}
    for game_id, entries in all_gt.items():
        validated = [e for e in entries if e.get("verdict") is not None]
        if validated:
            games_with_validated[game_id] = validated

    if not games_with_validated:
        print("No validated entries found in ground truth.")
        return {
            "blunder_script_version": BLUNDER_SCRIPT_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "results": {},
        }

    for game_id, entries in sorted(games_with_validated.items()):
        gz_path = str(game_path_for_id(game_id))

        with gzip.open(gz_path, "rt") as f:
            data = json.load(f)

        snapshots = data.get("snapshots", [])
        annotations = data.get("annotations", [])
        decisions = extract_decisions(gz_path)

        for entry in entries:
            di = entry["decision_index"]
            pk = play_key(game_id, di)

            # Find the decision
            decision = None
            for d in decisions:
                if d["decision_index"] == di:
                    decision = d
                    break

            if decision is None:
                print(
                    f"  WARN: decision {di} not found in {game_id}",
                    file=sys.stderr,
                )
                results[pk] = {"detected": False}
                continue

            aftermath = compute_aftermath_index(decision, snapshots)

            # Check if any annotation matches
            match = None
            for ann in annotations:
                if (
                    ann.get("snapshotIndex") == aftermath
                    and ann.get("player") == entry["player"]
                ):
                    match = ann
                    break

            if match is not None:
                results[pk] = {
                    "detected": True,
                    "severity": match.get("severity"),
                    "description": match.get("description"),
                }
            else:
                results[pk] = {"detected": False}

    return {
        "blunder_script_version": BLUNDER_SCRIPT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }


def main() -> None:
    baseline = derive_baseline()
    save_baseline(baseline)

    detected = sum(1 for r in baseline["results"].values() if r["detected"])
    total = len(baseline["results"])
    print(
        f"Baseline v{baseline['blunder_script_version']}: {detected}/{total} detected"
    )
    print("Written to blunder_baseline.json")


if __name__ == "__main__":
    main()
