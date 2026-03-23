#!/usr/bin/env python3
"""Seed the blunder ground truth from existing game annotations.

Reads all game files in website/public/games/, reverse-maps their
annotations to decision indices, and creates slim ground truth entries
(just {"decision_index": N}). Merges with existing ground truth
(preserves audited entries).

Usage:
    uv run python -m magebench.cli.analysis.blunder_seed
"""

import sys

from magebench.analysis.blunder.blunder_eval_common import (
    GAMES_DIR,
    decision_index,
    glob_game_files,
    load_game,
    make_seed_entry,
    merge_into_ground_truth,
    reverse_map_annotations,
)
from magebench.analysis.blunder.extract_decisions import extract_decisions


def seed_from_game(gz_path: str) -> tuple[str, list[dict]]:
    """Extract ground truth entries from a single game's annotations.

    Returns (game_id, entries).
    """
    data = load_game(gz_path)

    game_id = data.id
    annotations = data.annotations
    if not annotations:
        return game_id, []

    # Need llmEvents to extract decisions
    if not data.llm_events:
        print(f"  SKIP {game_id}: no llmEvents", file=sys.stderr)
        return game_id, []

    decisions = extract_decisions(gz_path)
    if not decisions:
        print(f"  SKIP {game_id}: no decisions extracted", file=sys.stderr)
        return game_id, []

    mapping = reverse_map_annotations(annotations, decisions)

    unmapped = len(annotations) - len(mapping)
    if unmapped > 0:
        print(
            f"  WARN {game_id}: {unmapped}/{len(annotations)} annotations unmapped",
            file=sys.stderr,
        )

    entries: list[dict] = []
    for decision_idx in mapping.values():
        entry = make_seed_entry(decision_index(decisions[decision_idx]))
        entries.append(entry)

    return game_id, entries


def main() -> None:
    game_files = glob_game_files(GAMES_DIR)
    assert game_files, f"No game files found in {GAMES_DIR}"

    total_added = 0
    total_games = 0
    total_annotations = 0

    for gz_path in game_files:
        game_id, entries = seed_from_game(str(gz_path))
        if not entries:
            continue

        total_annotations += len(entries)
        added = merge_into_ground_truth(game_id, entries)
        total_added += added
        total_games += 1

        if added > 0:
            print(f"  {game_id}: +{added} entries ({len(entries)} annotations)")

    print(f"\nSeeded {total_added} new entries from {total_annotations} annotations across {total_games} games")


if __name__ == "__main__":
    main()
