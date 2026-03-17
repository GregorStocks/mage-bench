#!/usr/bin/env python3
"""Run the blunder annotator against validated ground truth plays.

For each validated play, calls _eval_one_decision() and records whether
the annotator detected a blunder. Compares against the baseline.

THIS COSTS MONEY (LLM API calls).

Usage:
    uv run --project puppeteer python scripts/analysis/blunder_eval.py [--limit N] [--game GAME_ID]

Requires OPENROUTER_API_KEY environment variable.
"""

import json
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from openai import OpenAIError

from scripts.analysis.blunder_analysis import (
    BLUNDER_SCRIPT_VERSION,
    MAX_WORKERS,
    OPUS_MODEL,
    _eval_one_decision,
    init_api,
    load_game_context,
)
from scripts.analysis.blunder_eval_common import (
    BASELINE_PATH,
    TMP_DIR,
    decision_index,
    game_path_for_id,
    load_baseline,
    load_ground_truth,
    play_key,
)

_LOG_TZ = ZoneInfo("America/Los_Angeles")


def _detected_flag(
    result: dict | None, *, play_key: str, label: str, allow_missing: bool
) -> bool:
    """Return a result's detected flag, optionally allowing a missing entry."""
    if result is None:
        assert allow_missing, f"Missing {label} result for {play_key}"
        return False
    detected = result.get("detected")
    assert isinstance(detected, bool), (
        f"{label} result for {play_key} missing bool detected flag: {result!r}"
    )
    return detected


def compare_results(
    eval_results: dict[str, dict],
    baseline_results: dict[str, dict],
    all_gt: dict[str, list[dict]],
) -> dict:
    """Compare eval results against baseline and human verdicts."""
    fp = 0
    fn = 0
    baseline_fp = 0
    baseline_fn = 0
    total_validated = 0
    details: list[dict] = []

    for game_id, entries in sorted(all_gt.items()):
        for entry in entries:
            verdict = entry.get("verdict")
            if verdict is None or verdict == "questionable":
                continue

            total_validated += 1
            pk = play_key(game_id, entry["decision_index"])
            eval_entry = eval_results.get(pk)
            baseline_entry = baseline_results.get(pk)
            eval_detected = _detected_flag(
                eval_entry, play_key=pk, label="eval", allow_missing=False
            )
            base_detected = _detected_flag(
                baseline_entry, play_key=pk, label="baseline", allow_missing=True
            )

            is_blunder = verdict == "blunder"

            if is_blunder and not eval_detected:
                fn += 1
            if not is_blunder and eval_detected:
                fp += 1
            if is_blunder and not base_detected:
                baseline_fn += 1
            if not is_blunder and base_detected:
                baseline_fp += 1

            # Track changes
            if eval_detected != base_detected:
                details.append(
                    {
                        "play_key": pk,
                        "verdict": verdict,
                        "eval_detected": eval_detected,
                        "baseline_detected": base_detected,
                        "baseline_description": baseline_entry.get("description")
                        if baseline_entry
                        else None,
                        "eval_severity": eval_entry.get("severity")
                        if eval_entry
                        else None,
                        "eval_description": eval_entry.get("description")
                        if eval_entry
                        else None,
                        "human_notes": entry.get("human_notes"),
                    }
                )

    return {
        "total_validated": total_validated,
        "false_positives": fp,
        "false_negatives": fn,
        "baseline_false_positives": baseline_fp,
        "baseline_false_negatives": baseline_fn,
        "delta_fp": fp - baseline_fp,
        "delta_fn": fn - baseline_fn,
        "details": details,
    }


def print_report(comparison: dict) -> None:
    """Print human-readable eval report."""
    print(f"\n{'=' * 60}")
    print(f"Eval results (v{BLUNDER_SCRIPT_VERSION})")
    print(f"{'=' * 60}")
    print(f"  Validated plays: {comparison['total_validated']}")
    print(f"  False positives: {comparison['false_positives']}")
    print(f"  False negatives: {comparison['false_negatives']}")

    delta_fp = comparison["delta_fp"]
    delta_fn = comparison["delta_fn"]
    print("\n  Delta vs baseline:")
    print(
        f"    FP: {delta_fp:+d} ({comparison['baseline_false_positives']} -> {comparison['false_positives']})"
    )
    print(
        f"    FN: {delta_fn:+d} ({comparison['baseline_false_negatives']} -> {comparison['false_negatives']})"
    )

    details = comparison.get("details", [])
    if details:
        print(f"\n  Changes ({len(details)}):")
        indent = "          "
        for d in details:
            direction = "now detected" if d["eval_detected"] else "no longer detected"
            impact = (
                "GOOD"
                if (
                    (d["verdict"] == "blunder" and d["eval_detected"])
                    or (d["verdict"] == "not_blunder" and not d["eval_detected"])
                )
                else "BAD"
            )
            print(f"    [{impact}] {d['play_key']}: {direction} (human={d['verdict']})")
            base_desc = d.get("baseline_description")
            eval_desc = d.get("eval_description")
            if base_desc:
                wrapped = textwrap.fill(
                    base_desc,
                    width=80,
                    initial_indent=indent + "baseline: ",
                    subsequent_indent=indent + "          ",
                )
                print(wrapped)
            if eval_desc:
                wrapped = textwrap.fill(
                    eval_desc,
                    width=80,
                    initial_indent=indent + "eval:     ",
                    subsequent_indent=indent + "          ",
                )
                print(wrapped)
            human_notes = d.get("human_notes")
            if human_notes:
                wrapped = textwrap.fill(
                    human_notes,
                    width=80,
                    initial_indent=indent + "human:    ",
                    subsequent_indent=indent + "          ",
                )
                print(wrapped)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Run blunder eval against ground truth"
    )
    parser.add_argument("--limit", type=int, help="Limit number of plays to evaluate")
    parser.add_argument("--game", help="Filter to a specific game ID")
    args = parser.parse_args()

    all_gt = load_ground_truth()
    assert all_gt, "No ground truth files found. Run 'make blunder-seed' first."

    # Collect validated plays
    validated_by_game: dict[str, list[dict]] = {}
    total_validated = 0
    for game_id, entries in all_gt.items():
        if args.game and args.game != game_id:
            continue
        v = [e for e in entries if e.get("verdict") is not None]
        if v:
            validated_by_game[game_id] = v
            total_validated += len(v)

    assert total_validated > 0, (
        "No validated entries found. Run 'make blunder-audit' first."
    )

    if args.limit and args.limit < total_validated:
        print(f"Limiting to {args.limit} of {total_validated} validated plays")
        remaining = args.limit
        trimmed: dict[str, list[dict]] = {}
        for game_id, entries in validated_by_game.items():
            if remaining <= 0:
                break
            take = min(len(entries), remaining)
            trimmed[game_id] = entries[:take]
            remaining -= take
        validated_by_game = trimmed
        total_validated = args.limit

    print(
        f"Evaluating {total_validated} validated plays across {len(validated_by_game)} games"
    )

    # Load baseline
    baseline_results: dict[str, dict] = {}
    if BASELINE_PATH.exists():
        baseline = load_baseline()
        baseline_results = baseline["results"]
        print(
            f"Baseline: v{baseline.get('blunder_script_version', '?')} ({len(baseline_results)} results)"
        )
    else:
        print("No baseline found -- will compare against empty baseline")

    # Setup API
    client, prices = init_api()

    # Load game contexts and collect all work items
    print("Loading game data...")
    work_items: list[tuple[str, dict, dict]] = []  # (play_key, decision, game_ctx)
    for game_id, entries in sorted(validated_by_game.items()):
        gz_path = str(game_path_for_id(game_id))
        game_ctx = load_game_context(gz_path)
        decision_by_idx = {decision_index(d): d for d in game_ctx["decisions"]}

        for entry in entries:
            di = entry["decision_index"]
            assert di in decision_by_idx, f"Decision {di} not found in {game_id}"
            pk = play_key(game_id, di)
            work_items.append((pk, decision_by_idx[di], game_ctx))

    # Evaluate all plays across all games in parallel
    print(f"Submitting {len(work_items)} plays to {MAX_WORKERS} workers...")
    eval_results: dict[str, dict] = {}
    total_cost = 0.0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}
        for pk, decision, game_ctx in work_items:
            fut = pool.submit(
                _eval_one_decision,
                client,
                OPUS_MODEL,
                prices,
                game_ctx["overview"],
                decision,
                game_ctx["oracle_texts"],
                game_ctx["snapshots"],
                game_ctx["actions_by_turn"],
                game_ctx["num_players"],
                game_ctx["all_actions"],
                pk,
            )
            futures[fut] = pk

        for fut in as_completed(futures):
            pk = futures[fut]
            try:
                anns, cost, _parsed_ok, _raw = fut.result()
            except OpenAIError as e:
                print(f"  WARNING: {pk} failed: {e}")
                eval_results[pk] = {"detected": False}
                continue

            total_cost += cost
            if anns:
                eval_results[pk] = {
                    "detected": True,
                    "severity": anns[0].get("severity"),
                    "description": anns[0].get("description"),
                }
            else:
                eval_results[pk] = {"detected": False}

    print(f"\nTotal cost: ${total_cost:.3f}")

    # Save results
    TMP_DIR.mkdir(exist_ok=True)
    ts = datetime.now(_LOG_TZ).strftime("%Y%m%d_%H%M%S")
    output_path = TMP_DIR / f"blunder_eval_{ts}.json"
    output = {
        "blunder_script_version": BLUNDER_SCRIPT_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "cost_usd": total_cost,
        "results": eval_results,
    }
    output_path.write_text(json.dumps(output, indent=2) + "\n")
    print(f"Results saved to {output_path}")

    # Compare
    comparison = compare_results(eval_results, baseline_results, validated_by_game)
    print_report(comparison)


if __name__ == "__main__":
    main()
