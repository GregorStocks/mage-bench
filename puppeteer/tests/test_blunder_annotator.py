"""Tests for blunder annotation scripts: extract_decisions and annotate_game."""

import gzip
import json
import subprocess
from pathlib import Path

import pytest

from magebench.common.json5_utils import loads_json5
from magebench.game.game_export_types import Choice, Decision
from scripts.analysis.extract_decisions import extract_decisions
from scripts.game_exports import load_raw_game_export

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts" / "analysis"


def _make_test_game(
    *,
    extra_llm_events: list[dict] | None = None,
    extra_snapshots: list[dict] | None = None,
    include_decisions: bool = True,
) -> dict:
    """Create a minimal but valid game data structure for testing."""
    snapshots = [
        {
            "turn": 1,
            "phase": "PRECOMBAT_MAIN",
            "step": "PRECOMBAT_MAIN",
            "active_player": "Alice",
            "priority_player": "Alice",
            "seq": 1,
            "ts": "2026-01-01T12:00:01.000-08:00",
            "players": [
                {
                    "name": "Alice",
                    "life": 20,
                    "library_size": 53,
                    "hand": [{"name": "Mountain"}, {"name": "Lightning Bolt"}],
                    "battlefield": [],
                    "graveyard": [],
                    "commanders": [],
                },
                {
                    "name": "Bob",
                    "life": 20,
                    "library_size": 53,
                    "hand": [],
                    "battlefield": [{"name": "Grizzly Bears"}],
                    "graveyard": [],
                    "commanders": [],
                },
            ],
            "stack": [],
        },
        {
            "turn": 2,
            "phase": "PRECOMBAT_MAIN",
            "step": "PRECOMBAT_MAIN",
            "active_player": "Bob",
            "priority_player": "Bob",
            "seq": 10,
            "ts": "2026-01-01T12:00:10.000-08:00",
            "players": [
                {
                    "name": "Alice",
                    "life": 20,
                    "library_size": 52,
                    "hand": [{"name": "Lightning Bolt"}],
                    "battlefield": [{"name": "Mountain"}],
                    "graveyard": [],
                    "commanders": [],
                },
                {
                    "name": "Bob",
                    "life": 20,
                    "library_size": 53,
                    "hand": [],
                    "battlefield": [{"name": "Grizzly Bears"}],
                    "graveyard": [],
                    "commanders": [],
                },
            ],
            "stack": [],
        },
    ]
    if extra_snapshots:
        snapshots.extend(extra_snapshots)

    llm_events = [
        {
            "ts": "2026-01-01T12:00:01.500-08:00",
            "player": "Alice",
            "type": "tool_call",
            "tool": "get_action_choices",
            "args": {},
            "result": json.dumps(
                {
                    "action_pending": True,
                    "action_type": "GAME_SELECT",
                    "response_type": "select",
                    "message": "Play spells and abilities",
                    "choices": [
                        {"index": 0, "name": "Mountain"},
                        {"index": 1, "name": "Lightning Bolt"},
                    ],
                }
            ),
        },
        {
            "ts": "2026-01-01T12:00:01.700-08:00",
            "player": "Alice",
            "type": "llm_response",
            "reasoning": "I should play a land first to have mana available.",
            "toolCalls": [{"name": "choose_action"}],
        },
        {
            "ts": "2026-01-01T12:00:01.800-08:00",
            "player": "Alice",
            "type": "tool_call",
            "tool": "choose_action",
            "args": {"index": 0},
            "result": json.dumps({"success": True, "action_taken": "selected_0"}),
        },
    ]
    if extra_llm_events:
        llm_events.extend(extra_llm_events)

    game = {
        "version": 8,
        "id": "game_test_001",
        "timestamp": "20260101_120000",
        "gameType": "Two Player Duel",
        "deckType": "Constructed - Standard",
        "totalTurns": 2,
        "winner": "Alice",
        "players": [
            {
                "name": "Alice",
                "type": "pilot",
                "model": "test-model",
                "toolCallsOk": 0,
                "toolCallsFailed": 0,
                "thinkingTimeSecs": 0.0,
            },
            {
                "name": "Bob",
                "type": "pilot",
                "model": "test-model",
                "toolCallsOk": 0,
                "toolCallsFailed": 0,
                "thinkingTimeSecs": 0.0,
            },
        ],
        "cardImages": {},
        "snapshots": snapshots,
        "actions": [
            {
                "ts": "2026-01-01T12:00:02.000-08:00",
                "seq": 2,
                "message": "Alice plays Mountain",
            },
        ],
        "llmEvents": llm_events,
        "llmTrace": [],
        "gameOver": {"seq": 100, "message": "Player Alice is the winner"},
        "annotations": [],
        "blunderScriptVersion": 0,
        "harnessEpoch": 46,
        "youtubeUrl": "",
        "season": 1,
        "tournament": None,
    }
    if include_decisions:
        game["decisions"] = [
            {
                "index": 0,
                "snapshotIndex": 0,
                "player": "Alice",
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "actionType": "GAME_SELECT",
                "responseType": "select",
                "message": "Play spells and abilities",
                "choices": [
                    {"index": 0, "name": "Mountain"},
                    {"index": 1, "name": "Lightning Bolt"},
                ],
                "choiceCount": 2,
                "isForced": False,
                "chosen": 0,
                "chosenArgs": {"index": 0},
                "actionResult": {"success": True, "action_taken": "selected_0"},
                "llmEventIndices": [0, 1, 2],
                "subsequentActions": ["Alice plays Mountain"],
            }
        ]
    return game


def _write_gz(data: dict, path: Path) -> None:
    with gzip.open(path, "wt") as f:
        json.dump(data, f)


def _read_export(path: Path) -> dict:
    """Read a game export, checking both .json5.gz and .json5 variants."""
    if path.exists():
        return load_raw_game_export(path)
    # write_raw_game_export may have switched compression
    alt = path.with_suffix("") if path.suffix == ".gz" else Path(str(path) + ".gz")
    return load_raw_game_export(alt)


def _run_script(script_name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "python", str(SCRIPTS_DIR / script_name), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _run_annotate_game(gz_path: str, ann_path: str) -> subprocess.CompletedProcess[str]:
    return _run_script("annotate_game.py", gz_path, ann_path, "--no-leaderboard")


# --- extract_decisions tests ---


class TestExtractDecisions:
    def test_basic_extraction(self, tmp_path: Path) -> None:
        gz_path = tmp_path / "game_test.json5.gz"
        _write_gz(_make_test_game(), gz_path)

        result = _run_script("extract_decisions.py", str(gz_path))
        decisions = json.loads(result.stdout)

        assert len(decisions) == 1
        d = decisions[0]
        assert d["player"] == "Alice"
        assert d["choiceCount"] == 2
        assert d["chosen"] == 0
        assert d["isForced"] is False
        assert d["snapshotIndex"] == 0
        assert d["turn"] == 1
        assert d["phase"] == "PRECOMBAT_MAIN"
        assert d["message"] == "Play spells and abilities"

    def test_returns_decision_instances(self, tmp_path: Path) -> None:
        gz_path = tmp_path / "game_test.json5.gz"
        _write_gz(_make_test_game(), gz_path)

        decisions = extract_decisions(str(gz_path))

        assert len(decisions) == 1
        assert isinstance(decisions[0], Decision)
        assert isinstance(decisions[0].choices[0], Choice)
        assert decisions[0].choices[0].name == "Mountain"
        assert decisions[0].choices[1].name == "Lightning Bolt"

    def test_empty_decisions(self, tmp_path: Path) -> None:
        game = _make_test_game()
        game["decisions"] = []
        gz_path = tmp_path / "game_test.json5.gz"
        _write_gz(game, gz_path)

        result = _run_script("extract_decisions.py", str(gz_path))
        decisions = json.loads(result.stdout)
        assert decisions == []

    def test_forced_choice(self, tmp_path: Path) -> None:
        game = _make_test_game()
        game["decisions"][0]["isForced"] = True
        game["decisions"][0]["choices"] = [{"index": 0, "name": "Mountain"}]
        game["decisions"][0]["choiceCount"] = 1
        game["decisions"][0]["message"] = "Choose target creature"

        gz_path = tmp_path / "game_test.json5.gz"
        _write_gz(game, gz_path)

        result = _run_script("extract_decisions.py", str(gz_path))
        decisions = json.loads(result.stdout)
        assert len(decisions) == 1
        assert decisions[0]["isForced"] is True
        assert decisions[0]["choiceCount"] == 1

    def test_single_choice_with_pass_not_forced(self, tmp_path: Path) -> None:
        game = _make_test_game()
        game["decisions"][0]["choices"] = [{"index": 0, "name": "Mountain"}]
        game["decisions"][0]["choiceCount"] = 1
        # isForced stays False — player can pass

        gz_path = tmp_path / "game_test.json5.gz"
        _write_gz(game, gz_path)

        result = _run_script("extract_decisions.py", str(gz_path))
        decisions = json.loads(result.stdout)
        assert len(decisions) == 1
        assert decisions[0]["isForced"] is False
        assert decisions[0]["choiceCount"] == 1

    def test_multiple_players(self, tmp_path: Path) -> None:
        game = _make_test_game()
        game["decisions"].append(
            {
                "index": 1,
                "snapshotIndex": 1,
                "player": "Bob",
                "turn": 2,
                "phase": "COMBAT_DECLARE_ATTACKERS",
                "actionType": "GAME_SELECT",
                "responseType": "select",
                "message": "Attack with creatures",
                "choices": [
                    {"index": 0, "name": "Grizzly Bears"},
                    {"index": 1, "name": "Don't attack"},
                ],
                "choiceCount": 2,
                "isForced": False,
                "chosen": 0,
                "llmEventIndices": [3, 4, 5],
                "subsequentActions": [],
            }
        )
        gz_path = tmp_path / "game_test.json5.gz"
        _write_gz(game, gz_path)

        result = _run_script("extract_decisions.py", str(gz_path))
        decisions = json.loads(result.stdout)
        assert len(decisions) == 2
        assert decisions[0]["player"] == "Alice"
        assert decisions[1]["player"] == "Bob"

    def test_boolean_decision(self, tmp_path: Path) -> None:
        game = _make_test_game()
        game["decisions"][0]["responseType"] = "boolean"
        game["decisions"][0]["message"] = "Mulligan hand?"
        game["decisions"][0]["choices"] = []
        game["decisions"][0]["choiceCount"] = 0
        game["decisions"][0]["chosen"] = False

        gz_path = tmp_path / "game_test.json5.gz"
        _write_gz(game, gz_path)

        result = _run_script("extract_decisions.py", str(gz_path))
        decisions = json.loads(result.stdout)
        assert len(decisions) == 1
        assert decisions[0]["responseType"] == "boolean"
        assert decisions[0]["chosen"] is False

    def test_subsequent_actions(self, tmp_path: Path) -> None:
        game = _make_test_game()
        gz_path = tmp_path / "game_test.json5.gz"
        _write_gz(game, gz_path)

        result = _run_script("extract_decisions.py", str(gz_path))
        decisions = json.loads(result.stdout)
        assert len(decisions) == 1
        assert "Alice plays Mountain" in decisions[0]["subsequentActions"]


# --- annotate_game tests ---


def _make_valid_annotation(snapshot_index: int = 0, decision_index: int = 0) -> dict:
    return {
        "decisionIndex": decision_index,
        "snapshotIndex": snapshot_index,
        "player": "Alice",
        "type": "blunder",
        "severity": "moderate",
        "description": "Played land before combat when holding combat trick",
        "actionTaken": "Play Mountain",
        "betterLine": "Attack first, then play land in second main phase",
    }


class TestAnnotateGame:
    def test_basic_annotation_plain_json(self, tmp_path: Path) -> None:
        json_path = tmp_path / "game_test.json5"
        json_path.write_text(json.dumps(_make_test_game(include_decisions=True)))

        annotations = [_make_valid_annotation()]
        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps(annotations))

        _run_annotate_game(str(json_path), str(ann_path))

        data = loads_json5(json_path.read_text())
        assert "annotations" in data
        assert len(data["annotations"]) == 1
        assert not (tmp_path / "game_test.json5.gz").exists()

    def test_basic_annotation(self, tmp_path: Path) -> None:
        gz_path = tmp_path / "game_test.json5.gz"
        _write_gz(_make_test_game(include_decisions=True), gz_path)

        annotations = [_make_valid_annotation()]
        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps(annotations))

        _run_annotate_game(str(gz_path), str(ann_path))

        data = _read_export(gz_path)
        assert "annotations" in data
        assert len(data["annotations"]) == 1
        assert data["annotations"][0]["severity"] == "moderate"

    def test_annotation_with_llm_reasoning(self, tmp_path: Path) -> None:
        """v5 annotations with llmReasoning still pass validation."""
        gz_path = tmp_path / "game_test.json5.gz"
        _write_gz(_make_test_game(include_decisions=True), gz_path)

        annotation = _make_valid_annotation()
        annotation["llmReasoning"] = "The LLM prioritized mana development over combat advantage"
        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps([annotation]))

        _run_annotate_game(str(gz_path), str(ann_path))

        data = _read_export(gz_path)
        assert len(data["annotations"]) == 1
        assert data["annotations"][0]["llmReasoning"] == "The LLM prioritized mana development over combat advantage"

    def test_replaces_existing(self, tmp_path: Path) -> None:
        game = _make_test_game(include_decisions=True)
        game["annotations"] = [_make_valid_annotation()]
        gz_path = tmp_path / "game_test.json5.gz"
        _write_gz(game, gz_path)

        new_annotation = _make_valid_annotation()
        new_annotation["severity"] = "major"
        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps([new_annotation]))

        _run_annotate_game(str(gz_path), str(ann_path))

        data = _read_export(gz_path)
        assert len(data["annotations"]) == 1
        assert data["annotations"][0]["severity"] == "major"

    def test_invalid_snapshot_index(self, tmp_path: Path) -> None:
        gz_path = tmp_path / "game_test.json5.gz"
        _write_gz(_make_test_game(include_decisions=True), gz_path)

        annotation = _make_valid_annotation(snapshot_index=999)
        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps([annotation]))

        with pytest.raises(subprocess.CalledProcessError):
            _run_annotate_game(str(gz_path), str(ann_path))

    def test_invalid_decision_index(self, tmp_path: Path) -> None:
        gz_path = tmp_path / "game_test.json5.gz"
        _write_gz(_make_test_game(include_decisions=True), gz_path)

        annotation = _make_valid_annotation(decision_index=999)
        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps([annotation]))

        with pytest.raises(subprocess.CalledProcessError):
            _run_annotate_game(str(gz_path), str(ann_path))

    def test_invalid_severity(self, tmp_path: Path) -> None:
        gz_path = tmp_path / "game_test.json5.gz"
        _write_gz(_make_test_game(include_decisions=True), gz_path)

        annotation = _make_valid_annotation()
        annotation["severity"] = "catastrophic"
        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps([annotation]))

        with pytest.raises(subprocess.CalledProcessError):
            _run_annotate_game(str(gz_path), str(ann_path))

    def test_invalid_player(self, tmp_path: Path) -> None:
        gz_path = tmp_path / "game_test.json5.gz"
        _write_gz(_make_test_game(include_decisions=True), gz_path)

        annotation = _make_valid_annotation()
        annotation["player"] = "Charlie"
        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps([annotation]))

        with pytest.raises(subprocess.CalledProcessError):
            _run_annotate_game(str(gz_path), str(ann_path))

    def test_preserves_other_data(self, tmp_path: Path) -> None:
        game = _make_test_game(include_decisions=True)
        gz_path = tmp_path / "game_test.json5.gz"
        _write_gz(game, gz_path)

        annotations = [_make_valid_annotation()]
        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps(annotations))

        _run_annotate_game(str(gz_path), str(ann_path))

        data = _read_export(gz_path)
        assert data["id"] == "game_test_001"
        assert data["winner"] == "Alice"
        assert len(data["snapshots"]) == 2
        assert len(data["actions"]) == 1
        assert len(data["llmEvents"]) == 3

    def test_empty_annotations(self, tmp_path: Path) -> None:
        gz_path = tmp_path / "game_test.json5.gz"
        _write_gz(_make_test_game(include_decisions=True), gz_path)

        ann_path = tmp_path / "annotations.json"
        ann_path.write_text("[]")

        _run_annotate_game(str(gz_path), str(ann_path))

        data = _read_export(gz_path)
        assert data["annotations"] == []

    def test_missing_field(self, tmp_path: Path) -> None:
        gz_path = tmp_path / "game_test.json5.gz"
        _write_gz(_make_test_game(include_decisions=True), gz_path)

        annotation = _make_valid_annotation()
        del annotation["description"]
        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps([annotation]))

        with pytest.raises(subprocess.CalledProcessError):
            _run_annotate_game(str(gz_path), str(ann_path))
