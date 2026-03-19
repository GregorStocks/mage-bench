"""Tests for scripts/analysis/toolbox/mana_tapping.py."""

import gzip
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts" / "analysis" / "toolbox"

spec = importlib.util.spec_from_file_location("mana_tapping", SCRIPTS_DIR / "mana_tapping.py")
assert spec and spec.loader
mana_tapping = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mana_tapping)


def _make_gz(tmp_path: Path, events: list[dict], players: list[dict] | None = None) -> Path:
    """Create a minimal .json5.gz file with given llmEvents."""
    raw_players = players or [
        {"name": "Alice", "model": "test/model-a", "totalCostUsd": 0.1},
        {"name": "Bob", "model": "test/model-b", "totalCostUsd": 0.2},
    ]
    normalized_players = [
        {
            "name": player["name"],
            "type": player.get("type", "pilot"),
            "model": player.get("model", ""),
            "totalCostUsd": player.get("totalCostUsd", 0.0),
            "toolCallsOk": player.get("toolCallsOk", 0),
            "toolCallsFailed": player.get("toolCallsFailed", 0),
            "thinkingTimeSecs": player.get("thinkingTimeSecs", 0.0),
            **({"timedOut": player["timedOut"]} if "timedOut" in player else {}),
        }
        for player in raw_players
    ]
    data = {
        "version": 8,
        "id": "test_game",
        "timestamp": "2026-02-13T12:00:00-08:00",
        "gameType": "Two Player Duel",
        "deckType": "Constructed - Standard",
        "totalTurns": 1,
        "winner": "Alice",
        "players": normalized_players,
        "cardImages": {},
        "snapshots": [],
        "actions": [],
        "llmEvents": events,
        "gameOver": None,
        "annotations": [],
        "blunderScriptVersion": 0,
        "harnessEpoch": 46,
        "youtubeUrl": "",
        "season": 1,
        "tournament": None,
    }
    path = tmp_path / "game_test.json5.gz"
    with gzip.open(path, "wt") as f:
        json.dump(data, f)
    return str(path)


class TestAnalyzeGame:
    def test_no_events(self, tmp_path: Path) -> None:
        gz = _make_gz(tmp_path, [])
        stats = mana_tapping.analyze_game(gz)
        assert len(stats) == 2
        for ps in stats:
            assert ps.mana_plan_used == 0
            assert ps.auto_tap_used == 0
            assert ps.mana_deferred == 0
            assert ps.choose_ability == 0
            assert ps.spells_cancelled == 0

    def test_mana_plan_success(self, tmp_path: Path) -> None:
        events = [
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "args": {"index": 1, "mana_plan": [{"tap": "abc-123"}]},
                "result": json.dumps({"success": True, "action_taken": "selected_1", "mana_plan_set": True}),
            }
        ]
        gz = _make_gz(tmp_path, events)
        stats = mana_tapping.analyze_game(gz)
        alice = next(s for s in stats if s.player == "Alice")
        assert alice.mana_plan_used == 1
        assert alice.mana_plan_success == 1
        assert alice.mana_plan_failed == 0

    def test_mana_plan_failure(self, tmp_path: Path) -> None:
        events = [
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "args": {"index": 1, "mana_plan": [{"tap": "bad-id"}]},
                "result": json.dumps({"success": False, "error": "Index 1 out of range"}),
            }
        ]
        gz = _make_gz(tmp_path, events)
        stats = mana_tapping.analyze_game(gz)
        alice = next(s for s in stats if s.player == "Alice")
        assert alice.mana_plan_used == 1
        assert alice.mana_plan_success == 0
        assert alice.mana_plan_failed == 1
        assert alice.mana_plan_errors == ["Index 1 out of range"]

    def test_auto_tap_used(self, tmp_path: Path) -> None:
        events = [
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Bob",
                "args": {"index": 0, "auto_tap": True},
                "result": json.dumps({"success": True, "action_taken": "selected_0"}),
            }
        ]
        gz = _make_gz(tmp_path, events)
        stats = mana_tapping.analyze_game(gz)
        bob = next(s for s in stats if s.player == "Bob")
        assert bob.auto_tap_used == 1

    def test_mana_deferred_success(self, tmp_path: Path) -> None:
        events = [
            {
                "type": "tool_call",
                "tool": "get_action_choices",
                "player": "Alice",
                "args": {},
                "result": json.dumps({"action_type": "GAME_PLAY_MANA", "choices": [{"index": 0}]}),
            },
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "args": {"index": 0},
                "result": json.dumps({"success": True, "action_taken": "selected_0"}),
            },
        ]
        gz = _make_gz(tmp_path, events)
        stats = mana_tapping.analyze_game(gz)
        alice = next(s for s in stats if s.player == "Alice")
        assert alice.mana_deferred == 1
        assert alice.mana_deferred_success == 1
        assert alice.mana_deferred_failed == 0
        assert alice.mana_deferred_cancelled == 0

    def test_mana_deferred_cancelled(self, tmp_path: Path) -> None:
        events = [
            {
                "type": "tool_call",
                "tool": "get_action_choices",
                "player": "Alice",
                "args": {},
                "result": json.dumps({"action_type": "GAME_PLAY_MANA", "choices": []}),
            },
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "args": {"answer": False},
                "result": json.dumps({"success": True, "action_taken": "cancelled_spell"}),
            },
        ]
        gz = _make_gz(tmp_path, events)
        stats = mana_tapping.analyze_game(gz)
        alice = next(s for s in stats if s.player == "Alice")
        assert alice.mana_deferred == 1
        assert alice.mana_deferred_cancelled == 1

    def test_choose_ability_success(self, tmp_path: Path) -> None:
        events = [
            {
                "type": "tool_call",
                "tool": "get_action_choices",
                "player": "Bob",
                "args": {},
                "result": json.dumps(
                    {
                        "action_type": "GAME_CHOOSE_ABILITY",
                        "choices": [
                            {"index": 0, "description": "{T}: Add {G}"},
                            {"index": 1, "description": "{T}: Add {W}"},
                        ],
                    }
                ),
            },
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Bob",
                "args": {"index": 0},
                "result": json.dumps({"success": True, "action_taken": "selected_ability_0"}),
            },
        ]
        gz = _make_gz(tmp_path, events)
        stats = mana_tapping.analyze_game(gz)
        bob = next(s for s in stats if s.player == "Bob")
        assert bob.choose_ability == 1
        assert bob.choose_ability_success == 1
        assert bob.choose_ability_failed == 0

    def test_choose_ability_failed(self, tmp_path: Path) -> None:
        events = [
            {
                "type": "tool_call",
                "tool": "get_action_choices",
                "player": "Alice",
                "args": {},
                "result": json.dumps({"action_type": "GAME_CHOOSE_ABILITY", "choices": [{"index": 0}]}),
            },
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "args": {},
                "result": json.dumps({"success": False, "error": "Integer 'index' required"}),
            },
        ]
        gz = _make_gz(tmp_path, events)
        stats = mana_tapping.analyze_game(gz)
        alice = next(s for s in stats if s.player == "Alice")
        assert alice.choose_ability == 1
        assert alice.choose_ability_failed == 1

    def test_spell_cancelled(self, tmp_path: Path) -> None:
        events = [
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "args": {"answer": False},
                "result": json.dumps({"success": True, "action_taken": "cancelled_spell"}),
            }
        ]
        gz = _make_gz(tmp_path, events)
        stats = mana_tapping.analyze_game(gz)
        alice = next(s for s in stats if s.player == "Alice")
        assert alice.spells_cancelled == 1

    def test_ignores_other_players(self, tmp_path: Path) -> None:
        """Events from other players don't cross-contaminate."""
        events = [
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "args": {"index": 0, "auto_tap": True},
                "result": json.dumps({"success": True, "action_taken": "selected_0"}),
            },
        ]
        gz = _make_gz(tmp_path, events)
        stats = mana_tapping.analyze_game(gz)
        bob = next(s for s in stats if s.player == "Bob")
        assert bob.auto_tap_used == 0


class TestDirectoryMode:
    def test_aggregates_multiple_files(self, tmp_path: Path) -> None:
        # Game 1: Alice uses mana_plan
        events1 = [
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "args": {"index": 1, "mana_plan": [{"tap": "x"}]},
                "result": json.dumps({"success": True, "action_taken": "selected_1"}),
            }
        ]
        data1 = {
            "version": 8,
            "id": "g1",
            "timestamp": "2026-02-13T12:00:00-08:00",
            "gameType": "Two Player Duel",
            "deckType": "Constructed - Standard",
            "totalTurns": 1,
            "winner": "Alice",
            "players": [
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model-a",
                    "toolCallsOk": 0,
                    "toolCallsFailed": 0,
                    "thinkingTimeSecs": 0.0,
                }
            ],
            "cardImages": {},
            "snapshots": [],
            "actions": [],
            "llmEvents": events1,
            "gameOver": None,
            "annotations": [],
            "blunderScriptVersion": 0,
            "harnessEpoch": 46,
            "youtubeUrl": "",
            "season": 1,
            "tournament": None,
        }
        p1 = tmp_path / "game_1.json5.gz"
        with gzip.open(p1, "wt") as f:
            json.dump(data1, f)

        # Game 2: Alice uses auto_tap
        events2 = [
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "args": {"index": 0, "auto_tap": True},
                "result": json.dumps({"success": True, "action_taken": "selected_0"}),
            }
        ]
        data2 = {
            "version": 8,
            "id": "g2",
            "timestamp": "2026-02-13T12:00:00-08:00",
            "gameType": "Two Player Duel",
            "deckType": "Constructed - Standard",
            "totalTurns": 1,
            "winner": "Alice",
            "players": [
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model-a",
                    "toolCallsOk": 0,
                    "toolCallsFailed": 0,
                    "thinkingTimeSecs": 0.0,
                }
            ],
            "cardImages": {},
            "snapshots": [],
            "actions": [],
            "llmEvents": events2,
            "gameOver": None,
            "annotations": [],
            "blunderScriptVersion": 0,
            "harnessEpoch": 46,
            "youtubeUrl": "",
            "season": 1,
            "tournament": None,
        }
        p2 = tmp_path / "game_2.json5.gz"
        with gzip.open(p2, "wt") as f:
            json.dump(data2, f)

        # Analyze directory
        all_stats: list[mana_tapping.PlayerStats] = []
        for gz in sorted(tmp_path.glob("*.json5.gz")):
            all_stats.extend(mana_tapping.analyze_game(str(gz)))

        assert len(all_stats) == 2  # One player per game
        total_mp = sum(s.mana_plan_used for s in all_stats)
        total_at = sum(s.auto_tap_used for s in all_stats)
        assert total_mp == 1
        assert total_at == 1

    def test_main_directory(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        events = [
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "args": {"index": 0, "auto_tap": True},
                "result": json.dumps({"success": True, "action_taken": "selected_0"}),
            }
        ]
        data = {
            "version": 8,
            "id": "g1",
            "timestamp": "2026-02-13T12:00:00-08:00",
            "gameType": "Two Player Duel",
            "deckType": "Constructed - Standard",
            "totalTurns": 1,
            "winner": "Alice",
            "players": [
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model",
                    "toolCallsOk": 0,
                    "toolCallsFailed": 0,
                    "thinkingTimeSecs": 0.0,
                }
            ],
            "cardImages": {},
            "snapshots": [],
            "actions": [],
            "llmEvents": events,
            "gameOver": None,
            "annotations": [],
            "blunderScriptVersion": 0,
            "harnessEpoch": 46,
            "youtubeUrl": "",
            "season": 1,
            "tournament": None,
        }
        p = tmp_path / "game_test.json5.gz"
        with gzip.open(p, "wt") as f:
            json.dump(data, f)

        mana_tapping.main(str(tmp_path))
        out = capsys.readouterr().out
        assert "auto_tap Usage" in out
        assert "test/model" in out

    def test_main_single_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        gz = _make_gz(tmp_path, [])
        mana_tapping.main(gz)
        out = capsys.readouterr().out
        assert "mana_plan Usage" in out
