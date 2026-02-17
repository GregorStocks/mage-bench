"""Tests for blunder annotation scripts: extract_decisions and annotate_game."""

import gzip
import json
import subprocess
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts" / "analysis"


def _make_test_game(
    *,
    extra_llm_events: list[dict] | None = None,
    extra_snapshots: list[dict] | None = None,
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
                    "hand": [{"name": "Mountain"}, {"name": "Lightning Bolt"}],
                    "battlefield": [],
                    "graveyard": [],
                    "commanders": [],
                },
                {
                    "name": "Bob",
                    "life": 20,
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
                    "hand": [{"name": "Lightning Bolt"}],
                    "battlefield": [{"name": "Mountain"}],
                    "graveyard": [],
                    "commanders": [],
                },
                {
                    "name": "Bob",
                    "life": 20,
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

    return {
        "id": "game_test_001",
        "timestamp": "20260101_120000",
        "gameType": "Two Player Duel",
        "deckType": "Constructed - Standard",
        "totalTurns": 2,
        "winner": "Alice",
        "players": [
            {"name": "Alice", "type": "pilot", "model": "test-model"},
            {"name": "Bob", "type": "pilot", "model": "test-model"},
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
    }


def _write_gz(data: dict, path: Path) -> None:
    with gzip.open(path, "wt") as f:
        json.dump(data, f)


def _read_gz(path: Path) -> dict:
    with gzip.open(path, "rt") as f:
        return json.load(f)


def _run_script(script_name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "python", str(SCRIPTS_DIR / script_name), *args],
        capture_output=True,
        text=True,
        check=True,
    )


# --- extract_decisions tests ---


class TestExtractDecisions:
    def test_basic_extraction(self, tmp_path: Path) -> None:
        gz_path = tmp_path / "game.json.gz"
        _write_gz(_make_test_game(), gz_path)

        result = _run_script("extract_decisions.py", str(gz_path))
        decisions = json.loads(result.stdout)

        assert len(decisions) == 1
        d = decisions[0]
        assert d["player"] == "Alice"
        assert d["choice_count"] == 2
        assert d["chosen"] == 0
        assert d["is_forced"] is False
        assert d["reasoning"] == "I should play a land first to have mana available."
        assert d["snapshot_index"] == 0
        assert d["turn"] == 1
        assert d["phase"] == "PRECOMBAT_MAIN"
        assert d["message"] == "Play spells and abilities"

    def test_empty_events(self, tmp_path: Path) -> None:
        game = _make_test_game()
        game["llmEvents"] = []
        gz_path = tmp_path / "game.json.gz"
        _write_gz(game, gz_path)

        result = _run_script("extract_decisions.py", str(gz_path))
        decisions = json.loads(result.stdout)
        assert decisions == []

    def test_forced_choice(self, tmp_path: Path) -> None:
        game = _make_test_game()
        # Replace choices with a single forced option
        choices_result = json.loads(game["llmEvents"][0]["result"])
        choices_result["choices"] = [{"index": 0, "name": "Mountain"}]
        game["llmEvents"][0]["result"] = json.dumps(choices_result)

        gz_path = tmp_path / "game.json.gz"
        _write_gz(game, gz_path)

        result = _run_script("extract_decisions.py", str(gz_path))
        decisions = json.loads(result.stdout)
        assert len(decisions) == 1
        assert decisions[0]["is_forced"] is True
        assert decisions[0]["choice_count"] == 1

    def test_multiple_players(self, tmp_path: Path) -> None:
        bob_events = [
            {
                "ts": "2026-01-01T12:00:10.500-08:00",
                "player": "Bob",
                "type": "tool_call",
                "tool": "get_action_choices",
                "args": {},
                "result": json.dumps(
                    {
                        "action_pending": True,
                        "action_type": "GAME_SELECT",
                        "response_type": "select",
                        "message": "Attack with creatures",
                        "choices": [
                            {"index": 0, "name": "Grizzly Bears"},
                            {"index": 1, "name": "Don't attack"},
                        ],
                    }
                ),
            },
            {
                "ts": "2026-01-01T12:00:10.700-08:00",
                "player": "Bob",
                "type": "llm_response",
                "reasoning": "Let me attack with my bear.",
                "toolCalls": [{"name": "choose_action"}],
            },
            {
                "ts": "2026-01-01T12:00:10.800-08:00",
                "player": "Bob",
                "type": "tool_call",
                "tool": "choose_action",
                "args": {"index": 0},
                "result": json.dumps({"success": True}),
            },
        ]
        game = _make_test_game(extra_llm_events=bob_events)
        gz_path = tmp_path / "game.json.gz"
        _write_gz(game, gz_path)

        result = _run_script("extract_decisions.py", str(gz_path))
        decisions = json.loads(result.stdout)
        assert len(decisions) == 2
        assert decisions[0]["player"] == "Alice"
        assert decisions[1]["player"] == "Bob"

    def test_boolean_decision(self, tmp_path: Path) -> None:
        game = _make_test_game()
        # Replace with a boolean choice (mulligan)
        game["llmEvents"][0]["result"] = json.dumps(
            {
                "action_pending": True,
                "action_type": "GAME_ASK",
                "response_type": "boolean",
                "message": "Mulligan hand?",
                "choices": [],
            }
        )
        game["llmEvents"][2]["args"] = {"answer": False}

        gz_path = tmp_path / "game.json.gz"
        _write_gz(game, gz_path)

        result = _run_script("extract_decisions.py", str(gz_path))
        decisions = json.loads(result.stdout)
        assert len(decisions) == 1
        assert decisions[0]["response_type"] == "boolean"
        assert decisions[0]["chosen"] is False

    def test_subsequent_actions(self, tmp_path: Path) -> None:
        game = _make_test_game()
        # The existing action "Alice plays Mountain" has ts after the choose_action
        gz_path = tmp_path / "game.json.gz"
        _write_gz(game, gz_path)

        result = _run_script("extract_decisions.py", str(gz_path))
        decisions = json.loads(result.stdout)
        assert len(decisions) == 1
        assert "Alice plays Mountain" in decisions[0]["subsequent_actions"]

    def test_game_state_summary(self, tmp_path: Path) -> None:
        gz_path = tmp_path / "game.json.gz"
        _write_gz(_make_test_game(), gz_path)

        result = _run_script("extract_decisions.py", str(gz_path))
        decisions = json.loads(result.stdout)
        gs = decisions[0]["game_state"]
        assert gs["turn"] == 1
        # Alice has 2 cards in hand
        alice_state = next(p for p in gs["players"] if p["name"] == "Alice")
        assert alice_state["life"] == 20
        assert alice_state["hand_count"] == 2
        # Bob has Grizzly Bears on battlefield
        bob_state = next(p for p in gs["players"] if p["name"] == "Bob")
        assert "Grizzly Bears" in bob_state["battlefield"]


# --- annotate_game tests ---


def _make_valid_annotation(snapshot_index: int = 0) -> dict:
    return {
        "snapshotIndex": snapshot_index,
        "player": "Alice",
        "type": "blunder",
        "severity": "moderate",
        "description": "Played land before combat when holding combat trick",
        "actionTaken": "Play Mountain",
        "betterLine": "Attack first, then play land in second main phase",
    }


class TestAnnotateGame:
    def test_basic_annotation(self, tmp_path: Path) -> None:
        gz_path = tmp_path / "game.json.gz"
        _write_gz(_make_test_game(), gz_path)

        annotations = [_make_valid_annotation()]
        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps(annotations))

        _run_script("annotate_game.py", str(gz_path), str(ann_path))

        data = _read_gz(gz_path)
        assert "annotations" in data
        assert len(data["annotations"]) == 1
        assert data["annotations"][0]["severity"] == "moderate"

    def test_annotation_with_llm_reasoning(self, tmp_path: Path) -> None:
        """v5 annotations with llmReasoning still pass validation."""
        gz_path = tmp_path / "game.json.gz"
        _write_gz(_make_test_game(), gz_path)

        annotation = _make_valid_annotation()
        annotation["llmReasoning"] = "The LLM prioritized mana development over combat advantage"
        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps([annotation]))

        _run_script("annotate_game.py", str(gz_path), str(ann_path))

        data = _read_gz(gz_path)
        assert len(data["annotations"]) == 1
        assert data["annotations"][0]["llmReasoning"] == "The LLM prioritized mana development over combat advantage"

    def test_replaces_existing(self, tmp_path: Path) -> None:
        game = _make_test_game()
        game["annotations"] = [_make_valid_annotation()]
        gz_path = tmp_path / "game.json.gz"
        _write_gz(game, gz_path)

        new_annotation = _make_valid_annotation()
        new_annotation["severity"] = "major"
        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps([new_annotation]))

        _run_script("annotate_game.py", str(gz_path), str(ann_path))

        data = _read_gz(gz_path)
        assert len(data["annotations"]) == 1
        assert data["annotations"][0]["severity"] == "major"

    def test_invalid_snapshot_index(self, tmp_path: Path) -> None:
        gz_path = tmp_path / "game.json.gz"
        _write_gz(_make_test_game(), gz_path)

        annotation = _make_valid_annotation(snapshot_index=999)
        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps([annotation]))

        with pytest.raises(subprocess.CalledProcessError):
            _run_script("annotate_game.py", str(gz_path), str(ann_path))

    def test_invalid_severity(self, tmp_path: Path) -> None:
        gz_path = tmp_path / "game.json.gz"
        _write_gz(_make_test_game(), gz_path)

        annotation = _make_valid_annotation()
        annotation["severity"] = "catastrophic"
        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps([annotation]))

        with pytest.raises(subprocess.CalledProcessError):
            _run_script("annotate_game.py", str(gz_path), str(ann_path))

    def test_invalid_player(self, tmp_path: Path) -> None:
        gz_path = tmp_path / "game.json.gz"
        _write_gz(_make_test_game(), gz_path)

        annotation = _make_valid_annotation()
        annotation["player"] = "Charlie"
        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps([annotation]))

        with pytest.raises(subprocess.CalledProcessError):
            _run_script("annotate_game.py", str(gz_path), str(ann_path))

    def test_preserves_other_data(self, tmp_path: Path) -> None:
        game = _make_test_game()
        gz_path = tmp_path / "game.json.gz"
        _write_gz(game, gz_path)

        annotations = [_make_valid_annotation()]
        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps(annotations))

        _run_script("annotate_game.py", str(gz_path), str(ann_path))

        data = _read_gz(gz_path)
        assert data["id"] == "game_test_001"
        assert data["winner"] == "Alice"
        assert len(data["snapshots"]) == 2
        assert len(data["actions"]) == 1
        assert len(data["llmEvents"]) == 3

    def test_empty_annotations(self, tmp_path: Path) -> None:
        gz_path = tmp_path / "game.json.gz"
        _write_gz(_make_test_game(), gz_path)

        ann_path = tmp_path / "annotations.json"
        ann_path.write_text("[]")

        _run_script("annotate_game.py", str(gz_path), str(ann_path))

        data = _read_gz(gz_path)
        assert data["annotations"] == []

    def test_missing_field(self, tmp_path: Path) -> None:
        gz_path = tmp_path / "game.json.gz"
        _write_gz(_make_test_game(), gz_path)

        annotation = _make_valid_annotation()
        del annotation["description"]
        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps([annotation]))

        with pytest.raises(subprocess.CalledProcessError):
            _run_script("annotate_game.py", str(gz_path), str(ann_path))
