"""Shared utilities for the blunder evaluation harness.

Provides data structures, I/O, and matching logic used by the seed,
audit, baseline, eval, and promote scripts.
"""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from schemas.game_export_types import GameExport, JsonObject, load_game_export

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GROUND_TRUTH_DIR = REPO_ROOT / "scripts" / "analysis" / "ground_truth"
BASELINE_PATH = REPO_ROOT / "scripts" / "analysis" / "blunder_baseline.json"
GAMES_DIR = REPO_ROOT / "website" / "public" / "games"
TMP_DIR = REPO_ROOT / "tmp"
_SAFE_EXPORT_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_EXPORT_FILENAME_RE = re.compile(r"^game_[A-Za-z0-9_]+\.json(?:\.gz)?$")

GAME_ID_PATTERN = re.compile(r"^game_\d{8}_\d{6}(?:_g\d+)?$")
GAME_EXPORT_FILENAME_PATTERN = re.compile(
    r"^(game_\d{8}_\d{6}(?:_g\d+)?)\.json(?:\.gz)?$"
)


# --- Decision format compat helpers ---
# Canonical decisions (from export's decisions[]) use camelCase.
# Legacy decisions (from extract_decisions) use snake_case.
# These helpers read either format.


def is_canonical_decision(d: Mapping[str, object]) -> bool:
    """Check if a decision is in canonical (camelCase) format."""
    return "snapshotIndex" in d


def decision_index(d: Mapping[str, object]) -> int:
    """Get the decision index from either format."""
    value = d.get("index", d.get("decisionIndex", d.get("decision_index", 0)))
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"decision index must be an int, got {value!r}"
    )
    return value


def snapshot_index(d: Mapping[str, object]) -> int:
    """Get the snapshot index from either format."""
    value = d.get("snapshotIndex", d.get("snapshot_index", 0))
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"snapshot index must be an int, got {value!r}"
    )
    return value


def annotation_decision_index(annotation: Mapping[str, object]) -> int | None:
    """Get the canonical decision index for an annotation when present."""
    value = annotation.get("decisionIndex")
    if value is None:
        return None
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"annotation decisionIndex must be an int, got {value!r}"
    )
    return value


def is_forced(d: Mapping[str, object]) -> bool:
    """Check if a decision is forced (<=1 choice) in either format."""
    value = d.get("isForced", d.get("is_forced", False))
    assert isinstance(value, bool), f"isForced must be a bool, got {value!r}"
    return value


def action_result(d: Mapping[str, object]) -> JsonObject:
    """Get the action result from either format."""
    value = d.get("actionResult", d.get("action_result", {}))
    assert isinstance(value, dict), f"actionResult must be an object, got {value!r}"
    return value


def is_rolled_back(d: Mapping[str, object]) -> bool:
    """Check if a decision was rolled back in either format."""
    value = d.get("rolled_back", False)
    assert isinstance(value, bool), f"rolled_back must be a bool, got {value!r}"
    return value


def is_cast_rolled_back(d: Mapping[str, object]) -> bool:
    """Check if a cast was rolled back in either format."""
    value = d.get("castRolledBack", d.get("cast_rolled_back", False))
    assert isinstance(value, bool), f"castRolledBack must be a bool, got {value!r}"
    return value


def is_mana_ability_subdecision(d: Mapping[str, object]) -> bool:
    """Check if a decision is a mana ability sub-decision (picking which mana to produce).

    These are intermediate steps during mana payment or ability activation —
    not strategically interesting for blunder annotation.
    """
    msg = d.get("message", "")
    assert isinstance(msg, str), f"message must be a string, got {msg!r}"
    if msg.startswith("Choose which mana to produce from"):
        return True
    # "Choose spell or ability to play" where ALL choices are mana abilities
    if msg.startswith(("Choose spell or ability", "Choose ability")):
        choices = d.get("choices", [])
        assert isinstance(choices, list), f"choices must be a list, got {choices!r}"
        if choices and all(
            isinstance(c, dict)
            and "Add {" in (c.get("name", "") + c.get("description", ""))
            for c in choices
        ):
            return True
    return False


def subsequent_actions(d: Mapping[str, object]) -> list[str]:
    """Get subsequent actions from either format."""
    actions = d.get("subsequentActions", d.get("subsequent_actions", []))
    assert isinstance(actions, list), (
        f"subsequentActions must be a list, got {actions!r}"
    )
    result: list[str] = []
    for index, action in enumerate(actions):
        assert isinstance(action, str), (
            f"subsequentActions[{index}] must be a string, got {action!r}"
        )
        result.append(action)
    return result


def _allowed_export_roots() -> tuple[Path, ...]:
    """Directories that may contain analysis-ready game exports."""
    return (GAMES_DIR.resolve(), Path(tempfile.gettempdir()).resolve())


def _candidate_export_paths(path: str | Path) -> list[tuple[Path, Path]]:
    """Map a user-supplied path onto allowed export roots without touching the filesystem."""
    raw_path = Path(path)
    allowed_roots = _allowed_export_roots()
    if raw_path.is_absolute():
        root_candidates = [(root, raw_path) for root in allowed_roots]
    else:
        cwd_path = Path.cwd().resolve() / raw_path
        root_candidates = [(root, cwd_path) for root in allowed_roots]

    matches: list[tuple[Path, Path]] = []
    for root, candidate in root_candidates:
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        matches.append((root, relative))
    return matches


def _validate_export_path(path: str | Path) -> Path:
    """Resolve and validate a game export path before opening it."""
    matches = _candidate_export_paths(path)
    allowed_roots = _allowed_export_roots()
    assert matches, f"Game export must live under one of {allowed_roots}, got {path}"

    root, relative = matches[0]
    assert relative.parts, f"Game export path must include a filename: {path}"
    for part in relative.parts[:-1]:
        assert _SAFE_EXPORT_COMPONENT_RE.fullmatch(part), (
            f"Game export path has invalid directory component {part!r}: {path}"
        )

    filename = relative.parts[-1]
    assert _SAFE_EXPORT_FILENAME_RE.fullmatch(filename), (
        f"Game export filename must match game_*.json or game_*.json.gz: {path}"
    )

    candidate = root.joinpath(*relative.parts)
    resolved = candidate.resolve()
    assert resolved.is_relative_to(root), (
        f"Game export must stay under {root} after resolution, got {resolved}"
    )
    assert resolved.exists(), f"Game export not found: {resolved}"
    assert resolved.is_file(), f"Game export is not a file: {resolved}"
    return resolved


def load_game(path: str | Path) -> GameExport:
    """Load a game export file (.json or .json.gz)."""
    return load_game_export(_validate_export_path(path))


def validate_game_id(game_id: str) -> str:
    """Validate a canonical game export identifier."""
    assert isinstance(game_id, str), f"game_id must be a string, got {game_id!r}"
    assert GAME_ID_PATTERN.fullmatch(game_id), f"Invalid game_id: {game_id!r}"
    return game_id


def validate_export_filename(filename: str) -> str:
    """Validate a served export filename like game_...json(.gz)."""
    assert isinstance(filename, str), f"filename must be a string, got {filename!r}"
    assert GAME_EXPORT_FILENAME_PATTERN.fullmatch(filename), (
        f"Invalid game export filename: {filename!r}"
    )
    return filename


def glob_game_files(games_dir: Path) -> list[Path]:
    """Find all game export files (.json and .json.gz) in a directory, sorted."""
    gz_files = set(games_dir.glob("game_*.json.gz"))
    gz_stems = {p.name.removesuffix(".gz") for p in gz_files}
    json_files = [p for p in games_dir.glob("game_*.json") if p.name not in gz_stems]
    return sorted(gz_files | set(json_files))


def play_key(game_id: str, decision_index: int) -> str:
    """Canonical key for a play: 'game_id:decision_index'."""
    return f"{game_id}:{decision_index}"


def game_path_for_id(game_id: str) -> Path:
    """Resolve the export path for a game ID (.json.gz or .json)."""
    game_id = validate_game_id(game_id)
    gz_path = GAMES_DIR / f"{game_id}.json.gz"
    if gz_path.exists():
        return gz_path
    json_path = GAMES_DIR / f"{game_id}.json"
    assert json_path.exists(), f"Game file not found: {gz_path} or {json_path}"
    return json_path


def _gt_path(game_id: str) -> Path:
    """Ground truth file path for a game."""
    game_id = validate_game_id(game_id)
    return GROUND_TRUTH_DIR / f"{game_id}.json"


# --- Ground truth I/O ---


def load_ground_truth() -> dict[str, list[dict]]:
    """Load all ground truth files. Returns {game_id: [entries]}."""
    result: dict[str, list[dict]] = {}
    for p in sorted(GROUND_TRUTH_DIR.glob("*.json")):
        game_id = validate_game_id(p.stem)
        with open(p) as f:
            entries = json.load(f)
        assert isinstance(entries, list), f"{p}: expected JSON array"
        typed_entries: list[dict] = []
        for index, entry in enumerate(entries):
            assert isinstance(entry, dict), (
                f"{p}: entries[{index}] must be an object, got {entry!r}"
            )
            typed_entries.append(entry)
        result[game_id] = typed_entries
    return result


def load_game_ground_truth(game_id: str) -> list[dict]:
    """Load a single game's ground truth. Returns [] if file doesn't exist."""
    path = _gt_path(game_id)
    if not path.exists():
        return []
    with open(path) as f:
        entries = json.load(f)
    assert isinstance(entries, list), f"{path}: expected JSON array"
    typed_entries: list[dict] = []
    for index, entry in enumerate(entries):
        assert isinstance(entry, dict), (
            f"{path}: entries[{index}] must be an object, got {entry!r}"
        )
        typed_entries.append(entry)
    return typed_entries


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
    baseline = json.loads(BASELINE_PATH.read_text())
    assert isinstance(baseline, dict), f"{BASELINE_PATH}: expected JSON object"
    return baseline


def save_baseline(baseline: dict) -> None:
    """Write baseline to disk with sorted keys for stable diffs."""
    # Normalize cost field: "cost_usd" float -> "cost" string like "$1.23"
    if "cost_usd" in baseline:
        baseline = {**baseline, "cost": f"${baseline.pop('cost_usd'):.2f}"}
    BASELINE_PATH.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")


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


def compute_aftermath_index(
    decision: Mapping[str, object], snapshots: Sequence[Mapping[str, object]]
) -> int:
    """Compute the aftermath snapshot index for a decision.

    Mirrors the logic in _eval_one_decision from blunder_analysis.py:
    finds the first snapshot strictly after action_ts/action_seq, starting
    from the decision's snapshot_index.  action_seq represents the game state
    BEFORE the action processes, so we need > (not >=).
    """
    s_idx = snapshot_index(decision)
    action_seq_raw = decision.get("action_seq", 0) or decision.get("actionSeq", 0)
    action_seq = (
        action_seq_raw
        if isinstance(action_seq_raw, int) and not isinstance(action_seq_raw, bool)
        else 0
    )
    action_ts_raw = decision.get("action_ts", "")
    action_ts = action_ts_raw if isinstance(action_ts_raw, str) else ""
    if action_seq:
        for i in range(s_idx, len(snapshots)):
            snapshot_seq = snapshots[i].get("seq", 0)
            assert isinstance(snapshot_seq, int), (
                f"snapshot seq must be an int, got {snapshot_seq!r}"
            )
            if snapshot_seq > action_seq:
                return i
    elif action_ts:
        for i in range(s_idx, len(snapshots)):
            snapshot_ts = snapshots[i].get("ts", "")
            assert isinstance(snapshot_ts, str), (
                f"snapshot ts must be a string when present, got {snapshot_ts!r}"
            )
            if snapshot_ts > action_ts:
                return i
    return min(s_idx + 1, len(snapshots) - 1)


def reverse_map_annotations(
    annotations: Sequence[Mapping[str, object]],
    decisions: Sequence[Mapping[str, object]],
    snapshots: Sequence[Mapping[str, object]],
) -> dict[int, int]:
    """Map annotation list indices to decision indices.

    Returns {annotation_list_index: decision_index}.

    All annotations must carry a canonical decisionIndex.
    """
    result: dict[int, int] = {}

    for ann_idx, ann in enumerate(annotations):
        direct_decision_idx = annotation_decision_index(ann)
        assert direct_decision_idx is not None, (
            f"annotation {ann_idx} missing decisionIndex"
        )
        assert 0 <= direct_decision_idx < len(decisions), (
            f"annotation decisionIndex {direct_decision_idx} out of range for "
            f"{len(decisions)} decisions"
        )
        ann_player = ann["player"]
        assert isinstance(ann_player, str), (
            f"annotation player must be a string, got {ann_player!r}"
        )
        decision_player = decisions[direct_decision_idx]["player"]
        assert decision_player == ann_player, (
            f"annotation player {ann_player!r} does not match decision "
            f"{direct_decision_idx} player {decision_player!r}"
        )
        result[ann_idx] = direct_decision_idx

    return result


def lookup_annotation_for_decision(
    decision: Mapping[str, object],
    annotations: Sequence[Mapping[str, object]],
    snapshots: Sequence[Mapping[str, object]],
) -> Mapping[str, object] | None:
    """Find the game-file annotation matching a decision, if any."""
    idx = decision_index(decision)
    for ann in annotations:
        ann_idx = annotation_decision_index(ann)
        if ann_idx is not None and ann_idx == idx:
            return ann
    return None


def chosen_display(decision: Mapping[str, object]) -> str:
    """Human-readable name of what was chosen in a decision."""
    chosen = decision.get("chosen")
    choices = decision.get("choices", [])
    assert isinstance(choices, list), f"choices must be a list, got {choices!r}"
    if isinstance(chosen, bool):
        return str(chosen)
    if isinstance(chosen, int) and 0 <= chosen < len(choices):
        c = choices[chosen]
        if isinstance(c, dict):
            name = c.get("name", "")
            assert isinstance(name, str), f"choice name must be a string, got {name!r}"
            if name:
                return name
            description = c.get("description", "")
            assert isinstance(description, str), (
                f"choice description must be a string, got {description!r}"
            )
            if description:
                return description
        return f"option_{chosen}"
    if chosen is not None:
        return str(chosen)
    # Batch/text decisions store the response in chosenArgs/chosen_args, not chosen
    chosen_args = decision.get("chosenArgs") or decision.get("chosen_args")
    if not chosen_args:
        return "?"
    assert isinstance(chosen_args, dict), (
        f"chosenArgs must be an object when present, got {chosen_args!r}"
    )
    if chosen_args.get("attackers"):
        return f"Attack with: {chosen_args['attackers']}"
    if chosen_args.get("blockers"):
        return f"Block with: {chosen_args['blockers']}"
    if chosen_args.get("text"):
        return f"Text: {chosen_args['text']}"
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
