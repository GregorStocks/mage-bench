"""Runtime failure handling tests for the blunder eval harness."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from openai import OpenAIError

import magebench.analysis.blunder.blunder_eval as blunder_eval


def _make_game_ctx() -> dict:
    return {
        "decisions": [{"decision_index": 0, "player": "Alice"}],
        "overview": "Test overview",
        "oracle_texts": {},
        "snapshots": [],
        "actions_by_turn": {},
        "num_players": 2,
        "all_actions": [],
    }


def _configure_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(blunder_eval, "TMP_DIR", tmp_path)
    monkeypatch.setattr(blunder_eval, "BASELINE_PATH", tmp_path / "missing-baseline.json")
    monkeypatch.setattr(
        blunder_eval,
        "load_ground_truth",
        lambda: {"game_test_001": [{"decision_index": 0, "verdict": "blunder"}]},
    )
    monkeypatch.setattr(blunder_eval, "init_api", lambda: (MagicMock(), {}))
    monkeypatch.setattr(
        blunder_eval,
        "game_path_for_id",
        lambda _game_id: Path("/tmp/game_fake.json5.gz"),
    )
    monkeypatch.setattr(blunder_eval, "load_game_context", lambda _path: _make_game_ctx())
    monkeypatch.setattr("sys.argv", ["blunder_eval.py"])


def test_main_continues_on_openai_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _configure_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(
        blunder_eval,
        "evaluate_one_decision",
        MagicMock(side_effect=OpenAIError("temporary upstream failure")),
    )

    blunder_eval.main()

    output_files = sorted(tmp_path.glob("blunder_eval_*.json"))
    assert len(output_files) == 1
    saved = json.loads(output_files[0].read_text())
    assert saved["results"] == {"game_test_001:0": {"detected": False}}


def test_main_propagates_non_openai_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _configure_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(
        blunder_eval,
        "evaluate_one_decision",
        MagicMock(side_effect=AssertionError("unexpected bug")),
    )

    with pytest.raises(AssertionError, match="unexpected bug"):
        blunder_eval.main()
