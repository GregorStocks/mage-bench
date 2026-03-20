"""Shared utilities for the blunder evaluation harness.

Provides data structures, I/O, and matching logic used by the seed,
audit, baseline, eval, and promote scripts.
"""

import json
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from schemas.game_export_types import (
    Annotation,
    BuiltGameExport,
    Choice,
    Decision,
    GameExport,
    JsonObject,
    Snapshot,
    decision_support_get,
    export_record_field,
    load_built_game_export,
    load_game_export,
)
from scripts.game_exports import GAMES_DIR, glob_game_export_paths

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GROUND_TRUTH_DIR = REPO_ROOT / "scripts" / "analysis" / "ground_truth"
BASELINE_PATH = REPO_ROOT / "scripts" / "analysis" / "blunder_baseline.json"
TMP_DIR = REPO_ROOT / "tmp"
_SAFE_EXPORT_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_EXPORT_FILENAME_RE = re.compile(r"^game_[A-Za-z0-9_]+\.json5?(?:\.gz)?$")

GAME_ID_PATTERN = re.compile(r"^game_\d{8}_\d{6}(?:_g\d+)?$")
GAME_EXPORT_FILENAME_PATTERN = re.compile(
    r"^(game_\d{8}_\d{6}(?:_g\d+)?)\.json5?(?:\.gz)?$"
)


# --- Decision format helpers ---
# All decisions are now canonical Decision dataclass instances (camelCase).
# DecisionLike is kept as a union so callers passing plain dicts (tests,
# blunder_experiment.py) continue to work via Decision.__getitem__/get().

DecisionLike = Decision | Mapping[str, Any]


def decision_index(d: DecisionLike) -> int:
    """Get the decision index."""
    value = d.get("index", d.get("decisionIndex", 0))
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"decision index must be an int, got {value!r}"
    )
    return value


def snapshot_index(d: DecisionLike) -> int:
    """Get the snapshot index."""
    value = d.get("snapshotIndex", 0)
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"snapshot index must be an int, got {value!r}"
    )
    return value


def annotation_decision_index(annotation: Annotation) -> int:
    """Get the canonical decision index for an annotation."""
    return annotation.decisionIndex


def is_forced(d: DecisionLike) -> bool:
    """Check if a decision is forced (<=1 choice)."""
    value = d.get("isForced", False)
    assert isinstance(value, bool), f"isForced must be a bool, got {value!r}"
    return value


def action_result(d: DecisionLike) -> JsonObject:
    """Get the action result."""
    if "actionResult" in d:
        value = d["actionResult"]
    else:
        return {}
    assert isinstance(value, dict), f"actionResult must be an object, got {value!r}"
    return value


def is_cast_rolled_back(d: DecisionLike) -> bool:
    """Check if a cast was rolled back."""
    value = d.get("castRolledBack", False)
    assert isinstance(value, bool), f"castRolledBack must be a bool, got {value!r}"
    return value


def is_mana_ability_subdecision(d: DecisionLike) -> bool:
    """Check if a decision is a mana ability sub-decision (picking which mana to produce).

    These are intermediate steps during mana payment or ability activation —
    not strategically interesting for blunder annotation.
    """

    def _choice_text(choice: Choice) -> str:
        parts: list[str] = []
        name = decision_support_get(choice, "name")
        if isinstance(name, str):
            parts.append(name)
        description = decision_support_get(choice, "description")
        if isinstance(description, str):
            parts.append(description)
        return "".join(parts)

    msg = d.get("message")
    if not msg:
        return False
    assert isinstance(msg, str), f"message must be a string, got {msg!r}"
    if msg.startswith("Choose which mana to produce from"):
        return True
    # "Choose spell or ability to play" where ALL choices are mana abilities
    if msg.startswith(("Choose spell or ability", "Choose ability")):
        choices = d.get("choices")
        if choices is not None:
            assert isinstance(choices, list), f"choices must be a list, got {choices!r}"
        if choices and all(
            isinstance(c, Choice) and "Add {" in _choice_text(c) for c in choices
        ):
            return True
    return False


def subsequent_actions(d: DecisionLike) -> list[str]:
    """Get subsequent actions."""
    actions = d.get("subsequentActions")
    if actions is None:
        return []
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
        f"Game export filename must match game_*.json5 or game_*.json5.gz: {path}"
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
    """Load a game export file (.json or .json.gz). Requires annotations."""
    return load_game_export(_validate_export_path(path))


def load_game_for_annotation(path: str | Path) -> BuiltGameExport:
    """Load a game export that may not have annotations yet."""
    return load_built_game_export(_validate_export_path(path))


def export_record_name(record: object) -> str:
    """Get the schema-level name from a board/stack leaf record or raw string."""
    if isinstance(record, str):
        return record
    name = export_record_field(record, "name")
    assert isinstance(name, str), f"export record name must be a string, got {name!r}"
    return name


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
    return glob_game_export_paths(games_dir)


def play_key(game_id: str, decision_index: int) -> str:
    """Canonical key for a play: 'game_id:decision_index'."""
    return f"{game_id}:{decision_index}"


def game_path_for_id(game_id: str) -> Path:
    """Resolve the export path for a game ID (.json5.gz or .json5)."""
    game_id = validate_game_id(game_id)
    gz_path = GAMES_DIR / f"{game_id}.json5.gz"
    if gz_path.exists():
        return gz_path
    json5_path = GAMES_DIR / f"{game_id}.json5"
    assert json5_path.exists(), f"Game file not found: {gz_path} or {json5_path}"
    return json5_path


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
    decision: DecisionLike, snapshots: Sequence[Snapshot]
) -> int:
    """Compute the aftermath snapshot index for a decision.

    Finds the first snapshot strictly after actionSeq, starting from the
    decision's snapshotIndex.  actionSeq represents the game state BEFORE
    the action processes, so we need > (not >=).
    """
    s_idx = snapshot_index(decision)
    action_seq_raw = decision.get("actionSeq", 0)
    action_seq = (
        action_seq_raw
        if isinstance(action_seq_raw, int) and not isinstance(action_seq_raw, bool)
        else 0
    )
    if action_seq:
        for i in range(s_idx, len(snapshots)):
            if snapshots[i].seq > action_seq:
                return i
    return min(s_idx + 1, len(snapshots) - 1)


def reverse_map_annotations(
    annotations: Sequence[Annotation],
    decisions: Sequence[DecisionLike],
) -> dict[int, int]:
    """Map annotation list indices to decision indices.

    Returns {annotation_list_index: decision_index}.

    All annotations must carry a canonical decisionIndex.
    """
    result: dict[int, int] = {}

    for ann_idx, ann in enumerate(annotations):
        direct_decision_idx = annotation_decision_index(ann)
        assert 0 <= direct_decision_idx < len(decisions), (
            f"annotation decisionIndex {direct_decision_idx} out of range for {len(decisions)} decisions"
        )
        decision_player_raw = decisions[direct_decision_idx]["player"]
        assert isinstance(decision_player_raw, str), (
            f"decision player must be a string, got {decision_player_raw!r}"
        )
        assert decision_player_raw == ann.player, (
            f"annotation player {ann.player!r} does not match decision {direct_decision_idx} player {decision_player_raw!r}"
        )
        result[ann_idx] = direct_decision_idx

    return result


def lookup_annotation_for_decision(
    decision: DecisionLike,
    annotations: Sequence[Annotation],
) -> Annotation | None:
    """Find the game-file annotation matching a decision, if any."""
    idx = decision_index(decision)
    for ann in annotations:
        ann_idx = annotation_decision_index(ann)
        if ann_idx == idx:
            return ann
    return None


def chosen_display(decision: DecisionLike) -> str:
    """Human-readable name of what was chosen in a decision."""
    chosen = decision.get("chosen")
    choices = decision.get("choices")
    if choices is not None:
        assert isinstance(choices, list), f"choices must be a list, got {choices!r}"
    else:
        choices = []
    if isinstance(chosen, bool):
        return str(chosen)
    if isinstance(chosen, int) and 0 <= chosen < len(choices):
        c = choices[chosen]
        if isinstance(c, dict):
            name = c.get("name")
            if name is not None:
                assert isinstance(name, str), (
                    f"choice name must be a string, got {name!r}"
                )
                if name:
                    return name
            description = c.get("description")
            if description is not None:
                assert isinstance(description, str), (
                    f"choice description must be a string, got {description!r}"
                )
            if description:
                return description
        return f"option_{chosen}"
    if chosen is not None:
        return str(chosen)
    # Batch/text decisions store the response in chosenArgs, not chosen
    chosen_args = decision.get("chosenArgs")
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
