"""Tests for the blunder evaluation harness."""

from pathlib import Path

import pytest
from blunder_eval_common import (
    chosen_display,
    compute_aftermath_index,
    load_game_ground_truth,
    lookup_annotation_for_decision,
    make_audited_entry,
    make_seed_entry,
    merge_into_ground_truth,
    play_key,
    reverse_map_annotations,
    save_game_ground_truth,
)

# --- play_key ---


class TestPlayKey:
    def test_format(self) -> None:
        assert play_key("game_test_001", 42) == "game_test_001:42"

    def test_zero_index(self) -> None:
        assert play_key("game_test_001", 0) == "game_test_001:0"


# --- compute_aftermath_index ---


class TestComputeAftermathIndex:
    def test_with_action_ts(self) -> None:
        snapshots = [
            {"ts": "2026-01-01T00:00:01.000"},
            {"ts": "2026-01-01T00:00:05.000"},
            {"ts": "2026-01-01T00:00:10.000"},
        ]
        decision = {"snapshot_index": 0, "action_ts": "2026-01-01T00:00:04.000"}
        assert compute_aftermath_index(decision, snapshots) == 1

    def test_exact_ts_match(self) -> None:
        snapshots = [
            {"ts": "2026-01-01T00:00:01.000"},
            {"ts": "2026-01-01T00:00:05.000"},
        ]
        decision = {"snapshot_index": 0, "action_ts": "2026-01-01T00:00:05.000"}
        assert compute_aftermath_index(decision, snapshots) == 1

    def test_no_action_ts(self) -> None:
        snapshots = [{"ts": "2026-01-01T00:00:01.000"}]
        decision = {"snapshot_index": 0, "action_ts": ""}
        assert compute_aftermath_index(decision, snapshots) == 0

    def test_action_ts_beyond_all_snapshots(self) -> None:
        snapshots = [
            {"ts": "2026-01-01T00:00:01.000"},
            {"ts": "2026-01-01T00:00:02.000"},
        ]
        decision = {"snapshot_index": 0, "action_ts": "2026-01-01T00:00:99.000"}
        # No snapshot >= action_ts, falls back to snapshot_index
        assert compute_aftermath_index(decision, snapshots) == 0

    def test_starts_from_snapshot_index(self) -> None:
        """Search starts from decision's snapshot_index, not from 0."""
        snapshots = [
            {"ts": "2026-01-01T00:00:01.000"},
            {"ts": "2026-01-01T00:00:03.000"},
            {"ts": "2026-01-01T00:00:05.000"},
            {"ts": "2026-01-01T00:00:07.000"},
        ]
        decision = {"snapshot_index": 2, "action_ts": "2026-01-01T00:00:06.000"}
        # Should find snapshot 3 (ts >= action_ts), starting search from index 2
        assert compute_aftermath_index(decision, snapshots) == 3


# --- reverse_map_annotations ---


class TestReverseMapAnnotations:
    def _make_snapshots(self, n: int) -> list[dict]:
        return [{"ts": f"2026-01-01T00:00:{i:02d}.000"} for i in range(n)]

    def _make_decision(self, idx: int, snap_idx: int, player: str, action_ts: str = "") -> dict:
        return {
            "decision_index": idx,
            "snapshot_index": snap_idx,
            "action_ts": action_ts,
            "player": player,
        }

    def test_exact_match(self) -> None:
        snapshots = self._make_snapshots(10)
        decisions = [
            self._make_decision(0, 2, "Alice", "2026-01-01T00:00:05.000"),
        ]
        # aftermath of decision 0 = snapshot 5 (first >= action_ts)
        annotations = [{"snapshotIndex": 5, "player": "Alice"}]
        mapping = reverse_map_annotations(annotations, decisions, snapshots)
        assert mapping == {0: 0}

    def test_fallback_closest(self) -> None:
        snapshots = self._make_snapshots(10)
        decisions = [
            self._make_decision(0, 2, "Alice"),
            self._make_decision(1, 4, "Alice"),
        ]
        # Annotation at snapshot 6, no exact aftermath match
        annotations = [{"snapshotIndex": 6, "player": "Alice"}]
        mapping = reverse_map_annotations(annotations, decisions, snapshots)
        # Decision 1 (snapshot_index=4) is closer to 6 than decision 0 (snapshot_index=2)
        assert mapping == {0: 1}

    def test_player_filtering(self) -> None:
        snapshots = self._make_snapshots(10)
        decisions = [
            self._make_decision(0, 2, "Alice"),
            self._make_decision(1, 3, "Bob"),
        ]
        annotations = [{"snapshotIndex": 3, "player": "Bob"}]
        mapping = reverse_map_annotations(annotations, decisions, snapshots)
        assert mapping == {0: 1}

    def test_unmapped_annotation(self) -> None:
        snapshots = self._make_snapshots(5)
        decisions = [self._make_decision(0, 0, "Alice")]
        annotations = [{"snapshotIndex": 2, "player": "Charlie"}]
        mapping = reverse_map_annotations(annotations, decisions, snapshots)
        assert mapping == {}

    def test_multiple_annotations(self) -> None:
        snapshots = self._make_snapshots(10)
        decisions = [
            self._make_decision(0, 1, "Alice", "2026-01-01T00:00:03.000"),
            self._make_decision(1, 5, "Alice", "2026-01-01T00:00:07.000"),
        ]
        annotations = [
            {"snapshotIndex": 3, "player": "Alice"},
            {"snapshotIndex": 7, "player": "Alice"},
        ]
        mapping = reverse_map_annotations(annotations, decisions, snapshots)
        assert mapping == {0: 0, 1: 1}


# --- chosen_display ---


class TestChosenDisplay:
    def test_index_choice(self) -> None:
        d = {"chosen": 1, "choices": [{"name": "A"}, {"name": "B"}]}
        assert chosen_display(d) == "B"

    def test_boolean_choice(self) -> None:
        d = {"chosen": False, "choices": []}
        assert chosen_display(d) == "False"

    def test_none_choice(self) -> None:
        d = {"chosen": None, "choices": []}
        assert chosen_display(d) == "?"

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


class TestLookupAnnotationForDecision:
    def _make_snapshots(self, n: int) -> list[dict]:
        return [{"ts": f"2026-01-01T00:00:{i:02d}.000"} for i in range(n)]

    def test_exact_match(self) -> None:
        snapshots = self._make_snapshots(10)
        decision = {
            "decision_index": 0,
            "snapshot_index": 2,
            "action_ts": "2026-01-01T00:00:05.000",
            "player": "Alice",
        }
        annotations = [
            {"snapshotIndex": 5, "player": "Alice", "severity": "minor", "description": "bad play"},
        ]
        result = lookup_annotation_for_decision(decision, annotations, snapshots)
        assert result is not None
        assert result["severity"] == "minor"

    def test_no_match_wrong_player(self) -> None:
        snapshots = self._make_snapshots(10)
        decision = {
            "decision_index": 0,
            "snapshot_index": 2,
            "action_ts": "2026-01-01T00:00:05.000",
            "player": "Alice",
        }
        annotations = [
            {"snapshotIndex": 5, "player": "Bob", "severity": "minor"},
        ]
        result = lookup_annotation_for_decision(decision, annotations, snapshots)
        assert result is None

    def test_no_match_wrong_snapshot(self) -> None:
        snapshots = self._make_snapshots(10)
        decision = {
            "decision_index": 0,
            "snapshot_index": 2,
            "action_ts": "2026-01-01T00:00:05.000",
            "player": "Alice",
        }
        annotations = [
            {"snapshotIndex": 3, "player": "Alice", "severity": "minor"},
        ]
        result = lookup_annotation_for_decision(decision, annotations, snapshots)
        assert result is None

    def test_empty_annotations(self) -> None:
        snapshots = self._make_snapshots(5)
        decision = {
            "decision_index": 0,
            "snapshot_index": 0,
            "action_ts": "",
            "player": "Alice",
        }
        result = lookup_annotation_for_decision(decision, [], snapshots)
        assert result is None


# --- merge_into_ground_truth ---


class TestMergeIntoGroundTruth:
    def test_merge_new_entries(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("blunder_eval_common.GROUND_TRUTH_DIR", tmp_path)

        entries = [
            {"decision_index": 0},
            {"decision_index": 1},
        ]
        added = merge_into_ground_truth("game_test", entries)
        assert added == 2

        loaded = load_game_ground_truth("game_test")
        assert len(loaded) == 2

    def test_merge_preserves_existing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("blunder_eval_common.GROUND_TRUTH_DIR", tmp_path)

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
        save_game_ground_truth("game_test", existing)

        # Try to merge an entry with the same decision_index
        new_entries = [
            {"decision_index": 0},
            {"decision_index": 1},
        ]
        added = merge_into_ground_truth("game_test", new_entries)
        assert added == 1  # Only decision_index=1 was new

        loaded = load_game_ground_truth("game_test")
        assert len(loaded) == 2
        # Existing entry preserved
        existing_entry = next(e for e in loaded if e["decision_index"] == 0)
        assert existing_entry["verdict"] == "blunder"

    def test_merge_deduplicates_new(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("blunder_eval_common.GROUND_TRUTH_DIR", tmp_path)

        # Two new entries for same decision_index — keeps first
        entries = [
            {"decision_index": 5},
            {"decision_index": 5},
        ]
        added = merge_into_ground_truth("game_test", entries)
        assert added == 1

        loaded = load_game_ground_truth("game_test")
        assert len(loaded) == 1

    def test_merge_empty_new(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("blunder_eval_common.GROUND_TRUTH_DIR", tmp_path)

        existing = [{"decision_index": 0}]
        save_game_ground_truth("game_test", existing)

        added = merge_into_ground_truth("game_test", [])
        assert added == 0


# --- Baseline derivation ---


class TestBaselineDerivation:
    """Test the logic for matching game annotations to ground truth plays."""

    def test_detected_play(self) -> None:
        """An annotation matching the play's aftermath_index + player = detected."""
        snapshots = [{"ts": f"2026-01-01T00:00:{i:02d}.000"} for i in range(10)]
        decision = {
            "decision_index": 0,
            "snapshot_index": 2,
            "action_ts": "2026-01-01T00:00:05.000",
            "player": "Alice",
        }
        aftermath = compute_aftermath_index(decision, snapshots)
        assert aftermath == 5

        annotations = [{"snapshotIndex": 5, "player": "Alice", "severity": "moderate", "description": "bad"}]
        match = lookup_annotation_for_decision(decision, annotations, snapshots)
        assert match is not None

    def test_undetected_play(self) -> None:
        """No annotation at the play's aftermath_index + player = not detected."""
        snapshots = [{"ts": f"2026-01-01T00:00:{i:02d}.000"} for i in range(10)]
        decision = {
            "decision_index": 1,
            "snapshot_index": 3,
            "action_ts": "2026-01-01T00:00:06.000",
            "player": "Bob",
        }
        aftermath = compute_aftermath_index(decision, snapshots)
        assert aftermath == 6

        annotations = [{"snapshotIndex": 5, "player": "Alice", "severity": "moderate", "description": "bad"}]
        match = lookup_annotation_for_decision(decision, annotations, snapshots)
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
