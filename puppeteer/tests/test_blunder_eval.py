"""Tests for the blunder evaluation harness."""

import gzip
import json
from pathlib import Path

import pytest

import scripts.analysis.blunder_eval_common as blunder_eval_common
from schemas.game_export_types import Annotation, Snapshot
from scripts.analysis.blunder_eval_common import (
    chosen_display,
    compute_aftermath_index,
    decision_index,
    game_path_for_id,
    load_game_ground_truth,
    lookup_annotation_for_decision,
    make_audited_entry,
    make_seed_entry,
    merge_into_ground_truth,
    play_key,
    reverse_map_annotations,
    save_game_ground_truth,
)

VALID_GAME_ID = "game_20260214_005111_g1"


def _write_export(path: Path) -> None:
    data = {
        "version": 8,
        "id": "game_test_001",
        "timestamp": "2026-01-01T00:00:00Z",
        "gameType": "Two Player Duel",
        "deckType": "Constructed",
        "totalTurns": 0,
        "winner": None,
        "harnessEpoch": 46,
        "youtubeUrl": "",
        "players": [],
        "cardImages": {},
        "snapshots": [],
        "actions": [],
        "llmEvents": [],
        "gameOver": None,
        "annotations": [],
        "blunderScriptVersion": 1,
        "season": 1,
        "tournament": None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.name.endswith(".json.gz"):
        with gzip.open(path, "wt") as f:
            json.dump(data, f)
        return
    path.write_text(json.dumps(data))


# --- play_key ---


class TestPlayKey:
    def test_format(self) -> None:
        assert play_key("game_test_001", 42) == "game_test_001:42"

    def test_zero_index(self) -> None:
        assert play_key("game_test_001", 0) == "game_test_001:0"


class TestGameIdValidation:
    def test_game_path_for_id_rejects_invalid_game_id(self) -> None:
        with pytest.raises(AssertionError, match="Invalid game_id"):
            game_path_for_id("../etc/passwd")


class TestLoadGameValidation:
    def test_loads_export_from_games_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        games_dir = tmp_path / "website" / "public" / "games"
        monkeypatch.setattr(blunder_eval_common, "GAMES_DIR", games_dir)
        monkeypatch.setattr(
            blunder_eval_common.tempfile,
            "gettempdir",
            lambda: str(tmp_path / "system-temp"),
        )
        export_path = games_dir / "game_test_001.json.gz"
        _write_export(export_path)

        loaded = blunder_eval_common.load_game(export_path)

        assert loaded["id"] == "game_test_001"

    def test_loads_relative_repo_export_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        repo_root = tmp_path / "repo"
        games_dir = repo_root / "website" / "public" / "games"
        repo_root.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(blunder_eval_common, "GAMES_DIR", games_dir)
        monkeypatch.setattr(
            blunder_eval_common.tempfile,
            "gettempdir",
            lambda: str(tmp_path / "system-temp"),
        )
        monkeypatch.chdir(repo_root)
        export_path = games_dir / "game_test_001.json"
        _write_export(export_path)

        loaded = blunder_eval_common.load_game(Path("website/public/games/game_test_001.json"))

        assert loaded["id"] == "game_test_001"

    def test_loads_export_from_system_temp(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        temp_root = tmp_path / "system-temp"
        monkeypatch.setattr(blunder_eval_common, "GAMES_DIR", tmp_path / "repo-games")
        monkeypatch.setattr(
            blunder_eval_common.tempfile,
            "gettempdir",
            lambda: str(temp_root),
        )
        export_path = temp_root / "pytest-of-gregor" / "game_test.json.gz"
        _write_export(export_path)

        loaded = blunder_eval_common.load_game(export_path)

        assert loaded["id"] == "game_test_001"

    def test_rejects_path_outside_allowed_roots(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(blunder_eval_common, "GAMES_DIR", tmp_path / "repo-games")
        monkeypatch.setattr(
            blunder_eval_common.tempfile,
            "gettempdir",
            lambda: str(tmp_path / "system-temp"),
        )
        export_path = tmp_path / "outside" / "game_test_001.json"
        _write_export(export_path)

        with pytest.raises(AssertionError, match="must live under one of"):
            blunder_eval_common.load_game(export_path)

    def test_rejects_unexpected_suffix_inside_allowed_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        games_dir = tmp_path / "website" / "public" / "games"
        monkeypatch.setattr(blunder_eval_common, "GAMES_DIR", games_dir)
        monkeypatch.setattr(
            blunder_eval_common.tempfile,
            "gettempdir",
            lambda: str(tmp_path / "system-temp"),
        )
        export_path = games_dir / "game_test_001.txt"
        _write_export(export_path)

        with pytest.raises(
            AssertionError,
            match=r"filename must match game_\*\.json or game_\*\.json\.gz",
        ):
            blunder_eval_common.load_game(export_path)

    def test_rejects_symlink_escape_from_games_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        games_dir = tmp_path / "website" / "public" / "games"
        outside_path = tmp_path / "outside" / "game_test_001.json.gz"
        symlink_path = games_dir / "game_test_001.json.gz"
        monkeypatch.setattr(blunder_eval_common, "GAMES_DIR", games_dir)
        monkeypatch.setattr(
            blunder_eval_common.tempfile,
            "gettempdir",
            lambda: str(tmp_path / "system-temp"),
        )
        _write_export(outside_path)
        symlink_path.parent.mkdir(parents=True, exist_ok=True)
        symlink_path.symlink_to(outside_path)

        with pytest.raises(AssertionError, match="must stay under"):
            blunder_eval_common.load_game(symlink_path)


# --- compute_aftermath_index ---


def _snap(seq: int = 0, ts: str | None = None) -> Snapshot:
    """Build a minimal Snapshot for aftermath index tests."""
    return Snapshot(
        seq=seq,
        turn=1,
        phase=None,
        step=None,
        active_player=None,
        priority_player=None,
        players=[],
        stack=[],
        ts=ts,
    )


class TestComputeAftermathIndex:
    def test_with_action_seq(self) -> None:
        snapshots = [
            _snap(seq=1),
            _snap(seq=5),
            _snap(seq=10),
        ]
        decision = {"snapshotIndex": 0, "actionSeq": 4}
        assert compute_aftermath_index(decision, snapshots) == 1

    def test_exact_seq_match(self) -> None:
        snapshots = [
            _snap(seq=1),
            _snap(seq=5),
        ]
        # actionSeq=5, we need strictly greater, so snapshot seq=5 is not > 5
        decision = {"snapshotIndex": 0, "actionSeq": 4}
        assert compute_aftermath_index(decision, snapshots) == 1

    def test_no_action_seq(self) -> None:
        snapshots = [_snap(seq=1)]
        decision = {"snapshotIndex": 0}
        # No actionSeq -> falls back to snapshotIndex + 1
        assert compute_aftermath_index(decision, snapshots) == 0

    def test_action_seq_beyond_all_snapshots(self) -> None:
        snapshots = [
            _snap(seq=1),
            _snap(seq=2),
        ]
        decision = {"snapshotIndex": 0, "actionSeq": 99}
        # No snapshot > actionSeq, falls back to snapshotIndex + 1
        assert compute_aftermath_index(decision, snapshots) == 1

    def test_starts_from_snapshot_index(self) -> None:
        """Search starts from decision's snapshotIndex, not from 0."""
        snapshots = [
            _snap(seq=1),
            _snap(seq=3),
            _snap(seq=5),
            _snap(seq=7),
        ]
        decision = {"snapshotIndex": 2, "actionSeq": 6}
        # Should find snapshot 3 (seq=7 > 6), starting search from index 2
        assert compute_aftermath_index(decision, snapshots) == 3


# --- reverse_map_annotations ---


class TestReverseMapAnnotations:
    def _make_decision(self, idx: int, snap_idx: int, player: str) -> dict:
        return {
            "index": idx,
            "snapshotIndex": snap_idx,
            "player": player,
        }

    def test_direct_mapping(self) -> None:

        decisions = [
            self._make_decision(0, 2, "Alice"),
        ]
        annotations = [_ann(0, "Alice")]
        mapping = reverse_map_annotations(annotations, decisions)
        assert mapping == {0: 0}

    def test_multiple_annotations(self) -> None:

        decisions = [
            self._make_decision(0, 1, "Alice"),
            self._make_decision(1, 5, "Alice"),
        ]
        annotations = [_ann(0, "Alice"), _ann(1, "Alice")]
        mapping = reverse_map_annotations(annotations, decisions)
        assert mapping == {0: 0, 1: 1}

    def test_player_mismatch_raises(self) -> None:

        decisions = [
            self._make_decision(0, 2, "Alice"),
        ]
        annotations = [_ann(0, "Bob")]
        with pytest.raises(AssertionError, match="does not match"):
            reverse_map_annotations(annotations, decisions)

    def test_missing_decision_index_prevented_by_construction(self) -> None:
        """Annotation dataclass requires decisionIndex — can't construct without it."""
        with pytest.raises(TypeError):
            Annotation(  # type: ignore[call-arg]
                player="Alice",
                type="blunder",
                severity="minor",
                description="",
                actionTaken="",
                betterLine="",
            )


# --- chosen_display ---


class TestDecisionIndex:
    def test_reads_canonical_index_field(self) -> None:
        assert decision_index({"index": 7}) == 7

    def test_reads_decision_index_alias(self) -> None:
        assert decision_index({"decisionIndex": 7}) == 7


class TestChosenDisplay:
    def test_index_choice(self) -> None:
        d = {"chosen": 1, "choices": [{"name": "A"}, {"name": "B"}]}
        assert chosen_display(d) == "B"

    def test_boolean_choice(self) -> None:
        d = {"chosen": False, "choices": []}
        assert chosen_display(d) == "False"

    def test_none_choice_no_args(self) -> None:
        d = {"chosen": None, "choices": []}
        assert chosen_display(d) == "?"

    def test_none_choice_with_attackers_camel(self) -> None:
        d = {"chosen": None, "choices": [], "chosenArgs": {"attackers": "p5,p12"}}
        assert chosen_display(d) == "Attack with: p5,p12"

    def test_none_choice_with_blockers(self) -> None:
        d = {"chosen": None, "choices": [], "chosenArgs": {"blockers": "p3:p64"}}
        assert chosen_display(d) == "Block with: p3:p64"

    def test_none_choice_with_text(self) -> None:
        d = {"chosen": None, "choices": [], "chosenArgs": {"text": "Green"}}
        assert chosen_display(d) == "Text: Green"

    def test_out_of_range(self) -> None:
        d = {"chosen": 99, "choices": [{"name": "A"}]}
        assert chosen_display(d) == "99"


# --- make_seed_entry / make_audited_entry ---


class TestEntryConstructors:
    def test_seed_entry(self) -> None:
        entry = make_seed_entry(14)
        assert entry == {"decision_index": 14}

    def test_seed_entry_only_has_decision_index(self) -> None:
        entry = make_seed_entry(0)
        assert list(entry.keys()) == ["decision_index"]

    def test_audited_entry(self) -> None:
        entry = make_audited_entry(
            5,
            annotation_version=15,
            annotation_severity="moderate",
            annotation_description="Bad attack",
            verdict="blunder",
            human_notes="agreed",
        )
        assert entry["decision_index"] == 5
        assert entry["annotation_version"] == 15
        assert entry["annotation_severity"] == "moderate"
        assert entry["annotation_description"] == "Bad attack"
        assert entry["verdict"] == "blunder"
        assert entry["human_notes"] == "agreed"

    def test_audited_entry_no_annotation(self) -> None:
        entry = make_audited_entry(
            3,
            annotation_version=15,
            annotation_severity=None,
            annotation_description=None,
            verdict="blunder",
            human_notes=None,
        )
        assert entry["annotation_severity"] is None
        assert entry["annotation_description"] is None
        assert entry["verdict"] == "blunder"


# --- lookup_annotation_for_decision ---


def _ann(
    decision_index: int,
    player: str,
    *,
    severity: str = "minor",
    description: str = "",
    action_taken: str = "",
    better_line: str = "",
    snapshot_index: int | None = None,
) -> Annotation:
    """Create a minimal Annotation for testing."""
    return Annotation(
        decisionIndex=decision_index,
        player=player,
        type="blunder",
        severity=severity,
        description=description,
        actionTaken=action_taken,
        betterLine=better_line,
        snapshotIndex=snapshot_index,
    )


class TestLookupAnnotationForDecision:
    def test_exact_match(self) -> None:

        decision = {
            "index": 0,
            "snapshotIndex": 2,
            "player": "Alice",
        }
        annotations = [_ann(0, "Alice", severity="minor", description="bad play")]
        result = lookup_annotation_for_decision(decision, annotations)
        assert result is not None
        assert result.severity == "minor"

    def test_no_match_wrong_decision(self) -> None:

        decision = {
            "index": 0,
            "snapshotIndex": 2,
            "player": "Alice",
        }
        annotations = [_ann(1, "Bob")]
        result = lookup_annotation_for_decision(decision, annotations)
        assert result is None

    def test_no_match_different_index(self) -> None:

        decision = {
            "index": 0,
            "snapshotIndex": 2,
            "player": "Alice",
        }
        annotations = [_ann(3, "Alice")]
        result = lookup_annotation_for_decision(decision, annotations)
        assert result is None

    def test_empty_annotations(self) -> None:

        decision = {
            "index": 0,
            "snapshotIndex": 0,
            "player": "Alice",
        }
        result = lookup_annotation_for_decision(decision, [])
        assert result is None

    def test_matches_decision_index_even_if_snapshot_index_differs(self) -> None:

        decision = {
            "index": 1,
            "snapshotIndex": 2,
            "player": "Alice",
        }
        annotations = [_ann(1, "Alice", snapshot_index=0)]
        result = lookup_annotation_for_decision(decision, annotations)
        assert result is not None
        assert result.decisionIndex == 1


# --- merge_into_ground_truth ---


class TestMergeIntoGroundTruth:
    def test_merge_new_entries(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("scripts.analysis.blunder_eval_common.GROUND_TRUTH_DIR", tmp_path)

        entries = [
            {"decision_index": 0},
            {"decision_index": 1},
        ]
        added = merge_into_ground_truth(VALID_GAME_ID, entries)
        assert added == 2

        loaded = load_game_ground_truth(VALID_GAME_ID)
        assert len(loaded) == 2

    def test_merge_preserves_existing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("scripts.analysis.blunder_eval_common.GROUND_TRUTH_DIR", tmp_path)

        # Create existing audited entry
        existing = [
            make_audited_entry(
                0,
                annotation_version=15,
                annotation_severity="minor",
                annotation_description="test",
                verdict="blunder",
                human_notes=None,
            )
        ]
        save_game_ground_truth(VALID_GAME_ID, existing)

        # Try to merge an entry with the same decision_index
        new_entries = [
            {"decision_index": 0},
            {"decision_index": 1},
        ]
        added = merge_into_ground_truth(VALID_GAME_ID, new_entries)
        assert added == 1  # Only decision_index=1 was new

        loaded = load_game_ground_truth(VALID_GAME_ID)
        assert len(loaded) == 2
        # Existing entry preserved
        existing_entry = next(e for e in loaded if e["decision_index"] == 0)
        assert existing_entry["verdict"] == "blunder"

    def test_merge_deduplicates_new(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("scripts.analysis.blunder_eval_common.GROUND_TRUTH_DIR", tmp_path)

        # Two new entries for same decision_index — keeps first
        entries = [
            {"decision_index": 5},
            {"decision_index": 5},
        ]
        added = merge_into_ground_truth(VALID_GAME_ID, entries)
        assert added == 1

        loaded = load_game_ground_truth(VALID_GAME_ID)
        assert len(loaded) == 1

    def test_merge_empty_new(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("scripts.analysis.blunder_eval_common.GROUND_TRUTH_DIR", tmp_path)

        existing = [{"decision_index": 0}]
        save_game_ground_truth(VALID_GAME_ID, existing)

        added = merge_into_ground_truth(VALID_GAME_ID, [])
        assert added == 0


# --- Baseline derivation ---


class TestBaselineDerivation:
    """Test the logic for matching game annotations to ground truth plays."""

    def test_detected_play(self) -> None:
        """An annotation matching the decision index = detected."""
        decision = {
            "index": 0,
            "snapshotIndex": 2,
            "player": "Alice",
        }

        annotations = [_ann(0, "Alice", severity="moderate", description="bad")]
        match = lookup_annotation_for_decision(decision, annotations)
        assert match is not None

    def test_undetected_play(self) -> None:
        """No annotation with matching decision index = not detected."""
        decision = {
            "index": 1,
            "snapshotIndex": 3,
            "player": "Bob",
        }

        annotations = [_ann(0, "Alice", severity="moderate", description="bad")]
        match = lookup_annotation_for_decision(decision, annotations)
        assert match is None


# --- Eval comparison ---


class TestEvalComparison:
    """Test the comparison logic between eval results, baseline, and ground truth."""

    def _compare(
        self,
        eval_results: dict[str, dict],
        baseline_results: dict[str, dict],
        ground_truth_entries: list[tuple[str, str]],
    ) -> dict:
        """Simplified comparison matching the eval script's logic.

        ground_truth_entries: list of (play_key, verdict) tuples.
        """
        fp = 0
        fn = 0
        baseline_fp = 0
        baseline_fn = 0
        for pk, verdict in ground_truth_entries:
            eval_detected = eval_results.get(pk, {}).get("detected", False)
            base_detected = baseline_results.get(pk, {}).get("detected", False)

            if verdict == "blunder" and not eval_detected:
                fn += 1
            if verdict == "not_blunder" and eval_detected:
                fp += 1
            if verdict == "blunder" and not base_detected:
                baseline_fn += 1
            if verdict == "not_blunder" and base_detected:
                baseline_fp += 1

        return {
            "false_positives": fp,
            "false_negatives": fn,
            "delta_fp": fp - baseline_fp,
            "delta_fn": fn - baseline_fn,
        }

    def test_no_changes(self) -> None:
        results = {"g:0": {"detected": True}, "g:1": {"detected": False}}
        gt = [("g:0", "blunder"), ("g:1", "not_blunder")]
        c = self._compare(results, results, gt)
        assert c["false_positives"] == 0
        assert c["false_negatives"] == 0
        assert c["delta_fp"] == 0
        assert c["delta_fn"] == 0

    def test_new_false_positive(self) -> None:
        baseline = {"g:0": {"detected": False}}
        eval_res = {"g:0": {"detected": True}}
        gt = [("g:0", "not_blunder")]
        c = self._compare(eval_res, baseline, gt)
        assert c["false_positives"] == 1
        assert c["delta_fp"] == 1  # +1 FP vs baseline

    def test_fixed_false_negative(self) -> None:
        baseline = {"g:0": {"detected": False}}
        eval_res = {"g:0": {"detected": True}}
        gt = [("g:0", "blunder")]
        c = self._compare(eval_res, baseline, gt)
        assert c["false_negatives"] == 0
        assert c["delta_fn"] == -1  # -1 FN vs baseline (improvement)

    def test_new_false_negative(self) -> None:
        baseline = {"g:0": {"detected": True}}
        eval_res = {"g:0": {"detected": False}}
        gt = [("g:0", "blunder")]
        c = self._compare(eval_res, baseline, gt)
        assert c["false_negatives"] == 1
        assert c["delta_fn"] == 1  # +1 FN vs baseline (regression)
