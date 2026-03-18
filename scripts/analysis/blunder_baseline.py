#!/usr/bin/env python3
"""Derive blunder baseline from current game file annotations.

For each validated play in the ground truth, checks whether the current
game file contains an annotation at the matching snapshot index + player.

No LLM calls -- reads directly from game files.

Usage:
    uv run --project puppeteer python scripts/analysis/blunder_baseline.py
"""

import sys
from datetime import UTC, datetime

from scripts.analysis.blunder_analysis import BLUNDER_SCRIPT_VERSION
from scripts.analysis.blunder_eval_common import (
    decision_index,
    game_path_for_id,
    load_game,
    load_ground_truth,
    lookup_annotation_for_decision,
    play_key,
    save_baseline,
)
from scripts.analysis.extract_decisions import extract_decisions


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
            "generated_at": datetime.now(UTC).isoformat(),
            "results": {},
        }

    for game_id, entries in sorted(games_with_validated.items()):
        gz_path = str(game_path_for_id(game_id))
        data = load_game(gz_path)

        annotations = data.get("annotations")
        if annotations is None:
            annotations = []
        decisions = extract_decisions(gz_path)

        for entry in entries:
            di = entry["decision_index"]
            pk = play_key(game_id, di)

            # Find the decision
            decision = None
            for d in decisions:
                if decision_index(d) == di:
                    decision = d
                    break

            if decision is None:
                print(
                    f"  WARN: decision {di} not found in {game_id}",
                    file=sys.stderr,
                )
                results[pk] = {"detected": False}
                continue

            match = lookup_annotation_for_decision(decision, annotations)

            if match is not None:
                results[pk] = {
                    "detected": True,
                    "severity": match.severity,
                    "description": match.description,
                }
            else:
                results[pk] = {"detected": False}

    return {
        "blunder_script_version": BLUNDER_SCRIPT_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
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
