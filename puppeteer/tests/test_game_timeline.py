import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GAME_TIMELINE_PATH = REPO_ROOT / "scripts" / "analysis" / "game_timeline.py"


def _import_game_timeline():
    spec = importlib.util.spec_from_file_location("game_timeline", GAME_TIMELINE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


game_timeline = _import_game_timeline()


def _write_export(tmp_path: Path) -> Path:
    export = {
        "id": "game_test",
        "deckType": "Limited",
        "gameType": "Two Player Duel",
        "totalTurns": 2,
        "winner": "Alice",
        "players": [
            {
                "name": "Alice",
                "model": "model-a",
                "deckName": "Deck A",
                "totalCostUsd": 0.0,
            },
            {
                "name": "Bob",
                "model": "model-b",
                "deckName": "Deck B",
                "totalCostUsd": 0.0,
            },
        ],
        "snapshots": [
            {
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "active_player": "Alice",
                "seq": 5,
            },
            {
                "turn": 2,
                "phase": "PRECOMBAT_MAIN",
                "active_player": "Bob",
                "seq": 10,
            },
        ],
        "llmEvents": [
            {
                "type": "game_start",
                "player": "Alice",
                "ts": "2026-03-01T00:00:01.000000Z",
            },
            {
                "type": "tool_call",
                "player": "Alice",
                "ts": "2026-03-01T00:00:02.000000Z",
                "tool": "get_action_choices",
                "args": {},
                "result": "{}",
                "gameSeq": 5,
            },
            {
                "type": "tool_call",
                "player": "Bob",
                "ts": "2026-03-01T00:00:03.000000Z",
                "tool": "get_action_choices",
                "args": {},
                "result": "{}",
                "gameSeq": 10,
            },
        ],
    }
    path = tmp_path / "game_test.json"
    path.write_text(json.dumps(export))
    return path


def test_find_context_uses_timestamp_for_older_exports() -> None:
    snapshots = [
        {
            "turn": 1,
            "phase": "PRECOMBAT_MAIN",
            "active_player": "Alice",
            "ts": "2026-03-01T00:00:05.000000Z",
            "seq": 5,
        },
        {
            "turn": 2,
            "phase": "PRECOMBAT_MAIN",
            "active_player": "Bob",
            "ts": "2026-03-01T00:00:10.000000Z",
            "seq": 10,
        },
    ]
    event = {"ts": "2026-03-01T00:00:06.000000Z"}

    assert game_timeline.find_turn_for_event(snapshots, event) == 1
    assert game_timeline.find_context_for_event(snapshots, event) == "T1 PRECOMBAT_MAIN (Alice)"


def test_seq_based_exports_use_game_seq_for_context(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    export_path = _write_export(tmp_path)

    with patch.object(sys, "argv", ["game_timeline.py", str(export_path)]):
        game_timeline.main()

    lines = capsys.readouterr().out.splitlines()
    start_line = next(line for line in lines if "=== GAME START ===" in line)
    alice_line = next(line for line in lines if "Alice" in line and "get_action_choices()" in line)
    bob_line = next(line for line in lines if "Bob" in line and "get_action_choices()" in line)

    assert "T1 " not in start_line
    assert "T2 " not in start_line
    assert "T1 PRECOMBAT_MAIN (Alice)" in alice_line
    assert "T2 PRECOMBAT_MAIN (Bob)" in bob_line


def test_turn_filter_excludes_unresolved_events(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    export_path = _write_export(tmp_path)

    with patch.object(sys, "argv", ["game_timeline.py", str(export_path), "--turns", "1"]):
        game_timeline.main()

    out = capsys.readouterr().out

    assert "=== GAME START ===" not in out
    assert "T1 PRECOMBAT_MAIN (Alice)" in out
    assert "T2 PRECOMBAT_MAIN (Bob)" not in out
    assert "(1 events shown)" in out
