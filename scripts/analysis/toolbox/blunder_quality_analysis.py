#!/usr/bin/env python3
"""Deep quality analysis of blunder experiment results.

Loads all result files from tmp/blunder_experiment/ and performs cross-approach
consensus analysis, snapshot accuracy checks, and cost-effectiveness comparisons.

Usage:
    uv run --project puppeteer python scripts/analysis/toolbox/blunder_quality_analysis.py
"""

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
RESULTS_DIR = REPO_ROOT / "tmp" / "blunder_experiment"

# Consensus threshold: fraction of approaches that must flag a (game, snapshot)
# for it to be considered a consensus blunder
CONSENSUS_THRESHOLD = 0.30

SEVERITY_ORDER = {"questionable": 0, "minor": 1, "moderate": 2, "major": 3}


def load_all_results() -> dict[str, list[dict]]:
    """Load all result files, grouped by game_id."""
    games: dict[str, list[dict]] = defaultdict(list)
    for p in sorted(RESULTS_DIR.glob("*.json")):
        with open(p) as f:
            data = json.load(f)
        games[data["game_id"]].append(data)
    return dict(games)


def abbreviate(text: str, max_len: int = 80) -> str:
    """Abbreviate text to max_len chars."""
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def build_consensus(
    games: dict[str, list[dict]],
) -> dict[str, dict[int, dict]]:
    """Build consensus sets for each game.

    Returns: {game_id: {decisionIndex: {
        "approaches_that_found": {approach: [annotations]},
        "approaches_that_missed": [approach_names],
        "num_approaches_total": int,
    }}}
    """
    consensus: dict[str, dict[int, dict]] = {}

    for game_id, results in games.items():
        # Collect all decisionIndex -> {approach: [annotations]} mapping
        decision_to_approaches: dict[int, dict[str, list[dict]]] = defaultdict(
            lambda: defaultdict(list)
        )
        all_approaches = set()

        for r in results:
            approach = r["approach"]
            all_approaches.add(approach)
            for ann in r["annotations"]:
                dec = ann.get("decisionIndex")
                assert isinstance(dec, int), (
                    f"annotation missing decisionIndex in {approach}: {ann}"
                )
                decision_to_approaches[dec][approach].append(ann)

        num_approaches = len(all_approaches)
        game_consensus: dict[int, dict] = {}

        for dec_idx, approach_map in sorted(decision_to_approaches.items()):
            fraction = len(approach_map) / num_approaches
            found_approaches = set(approach_map.keys())
            missed_approaches = sorted(all_approaches - found_approaches)

            game_consensus[dec_idx] = {
                "approaches_that_found": dict(approach_map),
                "approaches_that_missed": missed_approaches,
                "num_approaches_total": num_approaches,
                "fraction": fraction,
                "is_consensus": fraction >= CONSENSUS_THRESHOLD,
            }

        consensus[game_id] = game_consensus

    return consensus


def print_section(title: str) -> None:
    """Print a section header."""
    print()
    print("=" * 90)
    print(f"  {title}")
    print("=" * 90)


def print_subsection(title: str) -> None:
    """Print a subsection header."""
    print()
    print(f"--- {title} ---")


def analyze_consensus_blunders(
    consensus: dict[str, dict[int, dict]],
) -> None:
    """Section 3: For each consensus blunder, show detailed cross-approach comparison."""
    print_section("CONSENSUS BLUNDERS (flagged by >= 30% of approaches)")

    total_consensus = 0
    total_non_consensus = 0

    for game_id in sorted(consensus.keys()):
        game_data = consensus[game_id]
        consensus_snaps = {s: d for s, d in game_data.items() if d["is_consensus"]}
        non_consensus_snaps = {
            s: d for s, d in game_data.items() if not d["is_consensus"]
        }
        total_consensus += len(consensus_snaps)
        total_non_consensus += len(non_consensus_snaps)

        if not consensus_snaps:
            continue

        print_subsection(
            f"{game_id}: {len(consensus_snaps)} consensus, {len(non_consensus_snaps)} non-consensus"
        )

        for dec_idx in sorted(consensus_snaps.keys()):
            info = consensus_snaps[dec_idx]
            found = info["approaches_that_found"]
            missed = info["approaches_that_missed"]
            total = info["num_approaches_total"]
            frac = info["fraction"]

            print(
                f"\n  decision={dec_idx}  ({len(found)}/{total} approaches = {frac:.0%})"
            )

            # Show which approaches found it with their details
            print("  FOUND BY:")
            for approach in sorted(found.keys()):
                anns = found[approach]
                for ann in anns:
                    sev = ann.get("severity", "?")
                    cat = ann.get("category", "?")
                    desc = abbreviate(ann.get("description", ""), 75)
                    dec_val = ann.get("decisionIndex", "?")
                    print(f"    {approach:<25} {sev:<14} {cat:<25} decision={dec_val}")
                    print(f"      {desc}")

            # Show which missed it
            if missed:
                print(f"  MISSED BY: {', '.join(missed)}")

            # Check decision index agreement
            all_decs = []
            for approach, anns in found.items():
                for ann in anns:
                    d = ann.get("decisionIndex")
                    if isinstance(d, int):
                        all_decs.append(d)
            if len(set(all_decs)) > 1:
                print(f"  DECISION DISAGREEMENT: values={sorted(set(all_decs))}")
            else:
                print(
                    f"  DECISION AGREEMENT: all say {all_decs[0] if all_decs else '?'}"
                )

            # Check severity agreement
            all_sevs = [
                ann.get("severity", "?") for anns in found.values() for ann in anns
            ]
            sev_set = set(all_sevs)
            if len(sev_set) > 1:
                sev_counts: dict[str, int] = defaultdict(int)
                for s in all_sevs:
                    sev_counts[s] += 1
                sev_str = ", ".join(f"{s}={c}" for s, c in sorted(sev_counts.items()))
                print(f"  SEVERITY SPREAD: {sev_str}")

    print(
        f"\n  TOTALS: {total_consensus} consensus blunders, "
        f"{total_non_consensus} non-consensus annotations across all games"
    )


def analyze_per_approach(
    games: dict[str, list[dict]],
    consensus: dict[str, dict[int, dict]],
) -> None:
    """Section 4: Per-approach stats across all games."""
    print_section("PER-APPROACH STATISTICS")

    # Collect stats per approach across all games
    approach_stats: dict[str, dict] = defaultdict(
        lambda: {
            "consensus_hits": 0,
            "consensus_misses": 0,
            "false_positives": 0,
            "total_annotations": 0,
            "description_lengths": [],
            "severities": [],
            "games_present": 0,
            "total_cost": 0.0,
        }
    )

    for game_id, results in games.items():
        game_consensus = consensus[game_id]
        consensus_decs = {s for s, d in game_consensus.items() if d["is_consensus"]}

        for r in results:
            approach = r["approach"]
            stats = approach_stats[approach]
            stats["games_present"] += 1
            stats["total_cost"] += r["cost_usd"]

            # Which consensus decisions did this approach find?
            found_decs = set()
            for ann in r["annotations"]:
                dec = ann.get("decisionIndex")
                stats["total_annotations"] += 1
                stats["description_lengths"].append(len(ann.get("description", "")))
                stats["severities"].append(ann.get("severity", "?"))

                if isinstance(dec, int):
                    found_decs.add(dec)
                    if dec in consensus_decs:
                        stats["consensus_hits"] += 1
                    else:
                        stats["false_positives"] += 1

            # How many consensus decisions did this approach miss in this game?
            for dec in consensus_decs:
                if dec not in found_decs:
                    stats["consensus_misses"] += 1

    # Print table
    print(
        f"\n  {'Approach':<25} {'Games':>5} {'Anns':>5} {'Cons':>5} {'Miss':>5} "
        f"{'FP':>5} {'FP%':>6} {'AvgDesc':>8} {'Cost':>8}"
    )
    print("  " + "-" * 85)

    for approach in sorted(approach_stats.keys()):
        s = approach_stats[approach]
        fp_rate = (
            s["false_positives"] / s["total_annotations"] * 100
            if s["total_annotations"] > 0
            else 0
        )
        avg_desc = (
            statistics.mean(s["description_lengths"]) if s["description_lengths"] else 0
        )
        print(
            f"  {approach:<25} {s['games_present']:>5} {s['total_annotations']:>5} "
            f"{s['consensus_hits']:>5} {s['consensus_misses']:>5} "
            f"{s['false_positives']:>5} {fp_rate:>5.1f}% "
            f"{avg_desc:>7.0f}c ${s['total_cost']:>7.3f}"
        )


def analyze_decision_accuracy(
    consensus: dict[str, dict[int, dict]],
) -> None:
    """Section 5: Decision attribution analysis.

    Approaches are given the decision index in the decision header, so within a
    single exact-match consensus group there's always 100% agreement. The
    interesting question is whether the SAME conceptual blunder gets split
    across nearby decisions -- e.g. the Momo legend-rule blunder in g8 gets
    decision 14 from some approaches and decision 16 from others.

    We detect this by finding consensus blunders at nearby decisions (within 3)
    whose descriptions share significant content, suggesting they're about the
    same underlying mistake.
    """
    print_section("DECISION ATTRIBUTION ANALYSIS")

    MERGE_WINDOW = 3
    # Minimum word overlap fraction to consider two annotations about the same blunder
    MIN_OVERLAP_FRAC = 0.25

    merge_candidates: list[tuple[str, list[int], dict[int, dict]]] = []

    for game_id in sorted(consensus.keys()):
        game_data = consensus[game_id]
        consensus_decs = sorted(s for s, d in game_data.items() if d["is_consensus"])

        # Find pairs of consensus decisions within MERGE_WINDOW
        used: set[int] = set()
        for i, d1 in enumerate(consensus_decs):
            if d1 in used:
                continue
            group = [d1]
            for j in range(i + 1, len(consensus_decs)):
                d2 = consensus_decs[j]
                if d2 - d1 <= MERGE_WINDOW and d2 not in used:
                    # Check description similarity
                    descs1 = [
                        a.get("description", "").lower()
                        for anns in game_data[d1]["approaches_that_found"].values()
                        for a in anns
                    ]
                    descs2 = [
                        a.get("description", "").lower()
                        for anns in game_data[d2]["approaches_that_found"].values()
                        for a in anns
                    ]

                    # Check if there's meaningful keyword overlap
                    words1: set[str] = set()
                    for desc in descs1:
                        words1.update(w for w in desc.split() if len(w) > 4)
                    words2: set[str] = set()
                    for desc in descs2:
                        words2.update(w for w in desc.split() if len(w) > 4)
                    if words1 and words2:
                        overlap = len(words1 & words2) / min(len(words1), len(words2))
                        if overlap >= MIN_OVERLAP_FRAC:
                            group.append(d2)
                            used.add(d2)

            if len(group) > 1:
                used.add(d1)
                dec_details = {d: game_data[d] for d in group}
                merge_candidates.append((game_id, group, dec_details))

    print(
        f"\n  Nearby consensus blunders that may be the same mistake: {len(merge_candidates)}"
    )

    if merge_candidates:
        for game_id, decs, details in merge_candidates:
            # Determine majority decision
            dec_votes: list[int] = []
            for d, info in details.items():
                dec_votes.extend([d] * len(info["approaches_that_found"]))
            majority = Counter(dec_votes).most_common(1)[0][0]

            print(f"\n  {game_id}: decisions {decs} (majority={majority})")
            for d in decs:
                info = details[d]
                approaches = sorted(info["approaches_that_found"].keys())
                label = "MAJORITY" if d == majority else "MINORITY"
                print(f"    decision={d} ({label}): {', '.join(approaches)}")

    # Summary: for approaches that appear in merge-candidates, how often are they
    # on the majority vs minority side?
    approach_majority: dict[str, int] = defaultdict(int)
    approach_minority: dict[str, int] = defaultdict(int)

    for game_id, decs, details in merge_candidates:
        dec_votes_list: list[int] = []
        for d, info in details.items():
            dec_votes_list.extend([d] * len(info["approaches_that_found"]))
        majority = Counter(dec_votes_list).most_common(1)[0][0]

        for d in decs:
            info = details[d]
            for approach in info["approaches_that_found"]:
                if d == majority:
                    approach_majority[approach] += 1
                else:
                    approach_minority[approach] += 1

    all_approaches_here = sorted(
        set(list(approach_majority.keys()) + list(approach_minority.keys()))
    )
    if all_approaches_here:
        print("\n  Per-approach alignment in split-attribution cases:")
        print(f"  {'Approach':<25} {'Majority':>8} {'Minority':>8} {'Align%':>7}")
        print("  " + "-" * 55)
        for approach in all_approaches_here:
            maj = approach_majority.get(approach, 0)
            mino = approach_minority.get(approach, 0)
            total = maj + mino
            pct = maj / total * 100 if total > 0 else 0
            print(f"  {approach:<25} {maj:>8} {mino:>8} {pct:>6.1f}%")


def analyze_hellkite_test(
    games: dict[str, list[dict]],
) -> None:
    """Section 6: The Hellkite test case for game g8.

    Two known blunders to examine:
    1. Magmatic Hellkite land destruction (snap=75): chose Multiversal Passage
       instead of Spirebluff Canal. The correct snap is 75 (the land choice),
       not 55 (an unrelated Sarkhan decision) or 73 (the Hellkite cast).
    2. Momo legend-rule (snap=14 or 16): cast a second legendary Momo.
       Most approaches assign snap=14 (the cast), some assign snap=16
       (the legend rule resolution). 14 is the better attribution since
       the mistake was the cast decision, not the forced choice of which to keep.
    """
    print_section("HELLKITE TEST CASE (game g8)")

    g8_id = None
    for game_id in games:
        if "g8" in game_id:
            g8_id = game_id
            break

    if g8_id is None:
        print("  No g8 game found!")
        return

    results = games[g8_id]
    num_approaches = len(results)

    # --- Hellkite land destruction (snap=75 neighborhood) ---
    print_subsection(
        f"Hellkite land destruction (correct snap=75, {num_approaches} approaches)"
    )
    print("  Context: Magmatic Hellkite ETB lets you destroy a nonbasic land.")
    print("  Sonnet Timmy chose Multiversal Passage over Spirebluff Canal.")
    print("  Spirebluff Canal was the better target (dual land, harder to replace).")
    print()

    found_hellkite: list[tuple[str, int, str, str, str]] = []
    missed_hellkite: list[str] = []
    for r in results:
        approach = r["approach"]
        found = False
        for ann in r["annotations"]:
            dec = ann.get("decisionIndex", -1)
            desc = ann.get("description", "").lower()
            # Match: annotations about the land destruction choice
            if ("passage" in desc or "spirebluff" in desc) and (
                "destroy" in desc or "wrong" in desc or "target" in desc
            ):
                sev = ann.get("severity", "?")
                cat = ann.get("category", "?")
                found_hellkite.append(
                    (approach, dec, sev, cat, ann.get("description", ""))
                )
                found = True
        if not found:
            missed_hellkite.append(approach)

    for approach, dec, sev, cat, desc in sorted(found_hellkite):
        print(f"  {approach:<25} decision={dec:<20} {sev:<14} {cat}")
        print(f"    {abbreviate(desc, 100)}")
    if missed_hellkite:
        print(
            f"\n  MISSED BY ({len(missed_hellkite)}): {', '.join(sorted(missed_hellkite))}"
        )

    # --- Momo legend-rule ---
    print_subsection(f"Momo legend-rule blunder ({num_approaches} approaches)")
    print("  Context: Cast second legendary Momo, wasting a card to legend rule.")
    print()

    momo_found: list[tuple[str, int, str, str]] = []
    missed_momo: list[str] = []

    for r in results:
        approach = r["approach"]
        found = False
        for ann in r["annotations"]:
            dec = ann.get("decisionIndex", -1)
            desc = ann.get("description", "").lower()
            cat = ann.get("category", "").lower()
            if "momo" in desc or "legend" in desc or "legend" in cat:
                found = True
                sev = ann.get("severity", "?")
                momo_found.append((approach, dec, sev, cat))
        if not found:
            missed_momo.append(approach)

    if momo_found:
        for approach, dec, sev, cat in sorted(momo_found):
            print(f"    {approach:<25} decision={dec:<5} {sev:<14} {cat}")
    if missed_momo:
        print(f"  MISSED ({len(missed_momo)}): {', '.join(sorted(missed_momo))}")

    print(f"\n  Summary: {len(momo_found)}/{num_approaches} found")


def analyze_cost_effectiveness(
    games: dict[str, list[dict]],
    consensus: dict[str, dict[int, dict]],
) -> None:
    """Section 7: Cost-effectiveness table."""
    print_section("COST-EFFECTIVENESS TABLE")

    # Collect per-approach totals
    approach_data: dict[str, dict] = defaultdict(
        lambda: {
            "games": 0,
            "total_cost": 0.0,
            "total_annotations": 0,
            "consensus_hits": 0,
            "false_positives": 0,
            "wall_time": 0.0,
        }
    )

    for game_id, results in games.items():
        game_consensus = consensus[game_id]
        consensus_decs = {s for s, d in game_consensus.items() if d["is_consensus"]}

        for r in results:
            approach = r["approach"]
            d = approach_data[approach]
            d["games"] += 1
            d["total_cost"] += r["cost_usd"]
            d["total_annotations"] += len(r["annotations"])
            d["wall_time"] += r["wall_time_seconds"]

            for ann in r["annotations"]:
                dec = ann.get("decisionIndex")
                if isinstance(dec, int) and dec in consensus_decs:
                    d["consensus_hits"] += 1
                else:
                    d["false_positives"] += 1

    print(
        f"\n  {'Approach':<25} {'Games':>5} {'$/game':>8} {'Anns':>5} "
        f"{'Ann/$':>7} {'CHits':>6} {'CHit/$':>7} {'FP%':>6} {'s/game':>8}"
    )
    print("  " + "-" * 90)

    for approach in sorted(approach_data.keys()):
        d = approach_data[approach]
        cost_per_game = d["total_cost"] / d["games"] if d["games"] > 0 else 0
        ann_per_dollar = (
            d["total_annotations"] / d["total_cost"] if d["total_cost"] > 0 else 0
        )
        chit_per_dollar = (
            d["consensus_hits"] / d["total_cost"] if d["total_cost"] > 0 else 0
        )
        fp_rate = (
            d["false_positives"] / d["total_annotations"] * 100
            if d["total_annotations"] > 0
            else 0
        )
        time_per_game = d["wall_time"] / d["games"] if d["games"] > 0 else 0

        print(
            f"  {approach:<25} {d['games']:>5} ${cost_per_game:>6.3f} "
            f"{d['total_annotations']:>5} {ann_per_dollar:>6.1f} "
            f"{d['consensus_hits']:>6} {chit_per_dollar:>6.1f} "
            f"{fp_rate:>5.1f}% {time_per_game:>7.1f}s"
        )


def analyze_severity_consistency(
    consensus: dict[str, dict[int, dict]],
) -> None:
    """For consensus blunders, analyze severity consistency across approaches."""
    print_section("SEVERITY CONSISTENCY FOR CONSENSUS BLUNDERS")

    # For each consensus blunder flagged by multiple approaches,
    # record the severities assigned
    approach_severity_alignment: dict[str, dict] = defaultdict(
        lambda: {
            "matches_majority": 0,
            "total": 0,
            "severity_map": defaultdict(int),
        }
    )

    for game_id in sorted(consensus.keys()):
        game_data = consensus[game_id]
        for info in game_data.values():
            if not info["is_consensus"]:
                continue
            found = info["approaches_that_found"]
            if len(found) < 2:
                continue

            # Get all severities
            all_sevs: list[tuple[str, str]] = [
                (approach, ann.get("severity", "?"))
                for approach, anns in found.items()
                for ann in anns
            ]

            if not all_sevs:
                continue

            # Find majority severity
            sev_counter = Counter(s for _, s in all_sevs)
            majority_sev = sev_counter.most_common(1)[0][0]

            for approach, sev in all_sevs:
                stats = approach_severity_alignment[approach]
                stats["total"] += 1
                stats["severity_map"][sev] += 1
                if sev == majority_sev:
                    stats["matches_majority"] += 1

    print(
        f"\n  {'Approach':<25} {'Total':>5} {'Match':>6} {'Match%':>7} "
        f"{'quest':>5} {'minor':>5} {'mod':>5} {'major':>5}"
    )
    print("  " + "-" * 75)

    for approach in sorted(approach_severity_alignment.keys()):
        s = approach_severity_alignment[approach]
        match_pct = s["matches_majority"] / s["total"] * 100 if s["total"] > 0 else 0
        sm = s["severity_map"]
        print(
            f"  {approach:<25} {s['total']:>5} {s['matches_majority']:>6} "
            f"{match_pct:>6.1f}% "
            f"{sm.get('questionable', 0):>5} {sm.get('minor', 0):>5} "
            f"{sm.get('moderate', 0):>5} {sm.get('major', 0):>5}"
        )


def print_summary_overview(games: dict[str, list[dict]]) -> None:
    """Print a high-level overview of the dataset."""
    print_section("DATASET OVERVIEW")

    total_files = sum(len(results) for results in games.values())
    all_approaches: set[str] = set()
    for results in games.values():
        for r in results:
            all_approaches.add(r["approach"])

    print(f"\n  Games: {len(games)}")
    print(f"  Result files: {total_files}")
    print(f"  Unique approaches: {len(all_approaches)}")
    print(f"  Approaches: {', '.join(sorted(all_approaches))}")

    print("\n  Per-game approach coverage:")
    for game_id in sorted(games.keys()):
        approaches = sorted(r["approach"] for r in games[game_id])
        print(f"    {game_id}: {len(approaches)} approaches")


def main() -> None:
    assert RESULTS_DIR.exists(), f"Results directory not found: {RESULTS_DIR}"

    games = load_all_results()
    assert len(games) > 0, "No result files found"

    consensus = build_consensus(games)

    # 1. Dataset overview
    print_summary_overview(games)

    # 2. Consensus blunder details
    analyze_consensus_blunders(consensus)

    # 3. Per-approach stats
    analyze_per_approach(games, consensus)

    # 4. Decision attribution analysis
    analyze_decision_accuracy(consensus)

    # 5. Severity consistency
    analyze_severity_consistency(consensus)

    # 6. Hellkite test case
    analyze_hellkite_test(games)

    # 7. Cost-effectiveness
    analyze_cost_effectiveness(games, consensus)


if __name__ == "__main__":
    main()
