"""Shared utilities for the blunder evaluation harness.

Provides data structures, I/O, and matching logic used by the seed,
audit, baseline, eval, and promote scripts.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GROUND_TRUTH_DIR = REPO_ROOT / "scripts" / "analysis" / "ground_truth"
BASELINE_PATH = REPO_ROOT / "scripts" / "analysis" / "blunder_baseline.json"
GAMES_DIR = REPO_ROOT / "website" / "public" / "games"
TMP_DIR = REPO_ROOT / "tmp"


def play_key(game_id: str, decision_index: int) -> str:
    """Canonical key for a play: 'game_id:decision_index'."""
    return f"{game_id}:{decision_index}"


def game_path_for_id(game_id: str) -> Path:
    """Resolve the .json.gz path for a game ID."""
    path = GAMES_DIR / f"{game_id}.json.gz"
    assert path.exists(), f"Game file not found: {path}"
    return path


def _gt_path(game_id: str) -> Path:
    """Ground truth file path for a game."""
    return GROUND_TRUTH_DIR / f"{game_id}.json"


# --- Ground truth I/O ---


def load_ground_truth() -> dict[str, list[dict]]:
    """Load all ground truth files. Returns {game_id: [entries]}."""
    result: dict[str, list[dict]] = {}
    for p in sorted(GROUND_TRUTH_DIR.glob("*.json")):
        game_id = p.stem
        with open(p) as f:
            result[game_id] = json.load(f)
    return result


def load_game_ground_truth(game_id: str) -> list[dict]:
    """Load a single game's ground truth. Returns [] if file doesn't exist."""
    path = _gt_path(game_id)
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def save_game_ground_truth(game_id: str, entries: list[dict]) -> None:
    """Write a single game's ground truth, sorted by decision_index."""
    GROUND_TRUTH_DIR.mkdir(parents=True, exist_ok=True)
    entries.sort(key=lambda e: e["decision_index"])
    path = _gt_path(game_id)
    path.write_text(json.dumps(entries, indent=2) + "\n")


# --- Baseline I/O ---


def load_baseline() -> dict:
    """Load baseline from disk."""
    assert BASELINE_PATH.exists(), f"Baseline not found: {BASELINE_PATH}"
    return json.loads(BASELINE_PATH.read_text())


def save_baseline(baseline: dict) -> None:
    """Write baseline to disk."""
    BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n")


# --- Ground truth entry constructors ---


def make_seed_entry(decision_index: int) -> dict:
    """Create a slim unaudited ground truth entry (just a pointer)."""
    return {"decision_index": decision_index}


def make_audited_entry(
    decision_index: int,
    *,
    annotation_version: int,
    annotation_severity: str | None,
    annotation_description: str | None,
    verdict: str,
    human_notes: str | None,
) -> dict:
    """Create a fully audited ground truth entry."""
    return {
        "decision_index": decision_index,
        "annotation_version": annotation_version,
        "annotation_severity": annotation_severity,
        "annotation_description": annotation_description,
        "verdict": verdict,
        "human_notes": human_notes,
    }


# --- Aftermath / reverse mapping ---


def compute_aftermath_index(decision: dict, snapshots: list[dict]) -> int:
    """Compute the aftermath snapshot index for a decision.

    Mirrors the logic in _eval_one_decision from blunder_analysis.py:
    finds the first snapshot at or after action_ts, starting from the
    decision's snapshot_index.
    """
    action_ts = decision.get("action_ts", "")
    if action_ts:
        for i in range(decision["snapshot_index"], len(snapshots)):
            if snapshots[i].get("ts", "") >= action_ts:
                return i
    return decision["snapshot_index"]


def reverse_map_annotations(
    annotations: list[dict],
    decisions: list[dict],
    snapshots: list[dict],
) -> dict[int, int]:
    """Map annotation list indices to decision indices.

    Returns {annotation_list_index: decision_index}.

    Strategy:
    1. Compute aftermath_idx for every decision.
    2. For each annotation, find exact match (aftermath_idx == snapshotIndex, same player).
    3. If no exact match, find closest decision where snapshot_index <= snapshotIndex (same player).
    """
    result: dict[int, int] = {}

    decision_aftermaths: list[int] = [
        compute_aftermath_index(d, snapshots) for d in decisions
    ]

    for ann_idx, ann in enumerate(annotations):
        ann_snap = ann["snapshotIndex"]
        ann_player = ann["player"]

        # Try exact match on aftermath index + player
        best_decision_idx: int | None = None
        for d_idx, d in enumerate(decisions):
            if d["player"] != ann_player:
                continue
            if decision_aftermaths[d_idx] == ann_snap:
                best_decision_idx = d_idx
                break

        # Fallback: closest decision for same player where snapshot_index <= ann_snap
        if best_decision_idx is None:
            best_dist = float("inf")
            for d_idx, d in enumerate(decisions):
                if d["player"] != ann_player:
                    continue
                if d["snapshot_index"] <= ann_snap:
                    dist = ann_snap - d["snapshot_index"]
                    if dist < best_dist:
                        best_dist = dist
                        best_decision_idx = d_idx

        if best_decision_idx is not None:
            result[ann_idx] = best_decision_idx

    return result


def lookup_annotation_for_decision(
    decision: dict,
    annotations: list[dict],
    snapshots: list[dict],
) -> dict | None:
    """Find the game-file annotation matching a decision, if any.

    Computes the decision's aftermath_index and scans annotations
    for a match on snapshotIndex + player.
    """
    aftermath = compute_aftermath_index(decision, snapshots)
    player = decision["player"]
    for ann in annotations:
        if ann.get("snapshotIndex") == aftermath and ann.get("player") == player:
            return ann
    return None


def chosen_display(decision: dict) -> str:
    """Human-readable name of what was chosen in a decision."""
    chosen = decision.get("chosen")
    choices = decision.get("choices", [])
    if isinstance(chosen, bool):
        return str(chosen)
    if isinstance(chosen, int) and 0 <= chosen < len(choices):
        c = choices[chosen]
        return c.get("name", c.get("description", f"option_{chosen}"))
    if chosen is not None:
        return str(chosen)
    return "?"


def merge_into_ground_truth(
    game_id: str,
    new_entries: list[dict],
) -> int:
    """Merge new entries into a game's ground truth file.

    Preserves existing entries. Only adds entries for decision_indices
    not already present. Deduplicates new entries by decision_index
    (keeps first occurrence).

    Returns the number of new entries added.
    """
    existing = load_game_ground_truth(game_id)
    existing_indices = {e["decision_index"] for e in existing}

    # Deduplicate new entries by decision_index, keeping first
    seen: set[int] = set()
    added: list[dict] = []
    for entry in new_entries:
        di = entry["decision_index"]
        if di in existing_indices or di in seen:
            continue
        seen.add(di)
        added.append(entry)

    if added:
        save_game_ground_truth(game_id, existing + added)
    return len(added)
