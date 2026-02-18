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
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from openai import OpenAI

from blunder_analysis import (
    BASE_URL,
    BLUNDER_SCRIPT_VERSION,
    MAX_WORKERS,
    OPUS_MODEL,
    _actions_by_turn,
    _collect_card_names,
    _eval_one_decision,
    _game_overview,
    _get_oracle_texts,
    _load_game,
)
from blunder_eval_common import (
    BASELINE_PATH,
    TMP_DIR,
    game_path_for_id,
    load_baseline,
    load_ground_truth,
    play_key,
)
from extract_decisions import extract_decisions
from puppeteer.llm_cost import fetch_openrouter_prices, get_model_price


def load_game_context(gz_path: str) -> dict:
    """Load and precompute all per-game context needed for eval."""
    data = _load_game(gz_path)
    decisions = extract_decisions(gz_path)
    snapshots = data.get("snapshots", [])
    overview = _game_overview(data)
    game_actions = data.get("actions", [])
    abt = _actions_by_turn(game_actions)
    num_players = len(data.get("players", []))

    card_names = _collect_card_names(data)
    oracle_texts = _get_oracle_texts(sorted(card_names))

    return {
        "data": data,
        "decisions": decisions,
        "snapshots": snapshots,
        "overview": overview,
        "oracle_texts": oracle_texts,
        "actions_by_turn": abt,
        "num_players": num_players,
        "all_actions": game_actions,
    }


def eval_one_play(
    entry: dict,
    game_ctx: dict,
    client: OpenAI,
    model: str,
    prices: dict[str, tuple[float, float]],
) -> tuple[dict, float]:
    """Evaluate a single play. Returns (result_dict, cost_usd)."""
    di = entry["decision_index"]
    decisions = game_ctx["decisions"]

    decision = None
    for d in decisions:
        if d["decision_index"] == di:
            decision = d
            break
    assert decision is not None, f"Decision {di} not found in game"

    anns, cost, parsed_ok, raw = _eval_one_decision(
        client,
        model,
        prices,
        game_ctx["overview"],
        decision,
        game_ctx["oracle_texts"],
        game_ctx["snapshots"],
        game_ctx["actions_by_turn"],
        game_ctx["num_players"],
        game_ctx["all_actions"],
    )

    if anns:
        return {
            "detected": True,
            "severity": anns[0].get("severity"),
            "description": anns[0].get("description"),
        }, cost
    else:
        return {"detected": False}, cost


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
            if verdict is None:
                continue

            total_validated += 1
            pk = play_key(game_id, entry["decision_index"])
            eval_detected = eval_results.get(pk, {}).get("detected", False)
            base_detected = baseline_results.get(pk, {}).get("detected", False)

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
                        "eval_severity": eval_results.get(pk, {}).get("severity"),
                        "description": eval_results.get(pk, {}).get("description")
                        or entry.get("annotation_description"),
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
            desc = d.get("description") or ""
            if len(desc) > 80:
                desc = desc[:77] + "..."
            print(f"    [{impact}] {d['play_key']}: {direction} (human={d['verdict']})")
            if desc:
                print(f"          {desc}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Run blunder eval against ground truth"
    )
    parser.add_argument("--limit", type=int, help="Limit number of plays to evaluate")
    parser.add_argument("--game", help="Filter to a specific game ID")
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    assert api_key, "OPENROUTER_API_KEY environment variable required"

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
        # Trim entries across games
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
        baseline_results = baseline.get("results", {})
        print(
            f"Baseline: v{baseline.get('blunder_script_version', '?')} ({len(baseline_results)} results)"
        )
    else:
        print("No baseline found -- will compare against empty baseline")

    # Setup API
    prices = fetch_openrouter_prices()
    assert get_model_price(OPUS_MODEL, prices) is not None, (
        f"Could not fetch pricing for {OPUS_MODEL}"
    )
    client = OpenAI(base_url=BASE_URL, api_key=api_key)

    # Evaluate
    eval_results: dict[str, dict] = {}
    total_cost = 0.0

    for game_id, entries in sorted(validated_by_game.items()):
        print(f"\n{game_id}: {len(entries)} plays...")
        gz_path = str(game_path_for_id(game_id))
        game_ctx = load_game_context(gz_path)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {}
            for entry in entries:
                fut = pool.submit(
                    eval_one_play, entry, game_ctx, client, OPUS_MODEL, prices
                )
                futures[fut] = entry

            for fut in as_completed(futures):
                entry = futures[fut]
                pk = play_key(game_id, entry["decision_index"])
                result, cost = fut.result()
                eval_results[pk] = result
                total_cost += cost

    print(f"\nTotal cost: ${total_cost:.3f}")

    # Save results
    TMP_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = TMP_DIR / f"blunder_eval_{ts}.json"
    output = {
        "blunder_script_version": BLUNDER_SCRIPT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cost_usd": total_cost,
        "results": eval_results,
    }
    output_path.write_text(json.dumps(output, indent=2) + "\n")
    print(f"Results saved to {output_path}")

    # Compare
    comparison = compare_results(eval_results, baseline_results, all_gt)
    print_report(comparison)


if __name__ == "__main__":
    main()
