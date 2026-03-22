import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from magebench.game.game_export_types import GameStartEvent, Snapshot

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GAME_TIMELINE_PATH = REPO_ROOT / "scripts" / "analysis" / "toolbox" / "game_timeline.py"


def _import_game_timeline():
    spec = importlib.util.spec_from_file_location("game_timeline", GAME_TIMELINE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


game_timeline = _import_game_timeline()


def _write_export(tmp_path: Path, *, annotated: bool = True) -> Path:
    export = {
        "version": 9,
        "id": "game_test",
        "timestamp": "2026-03-01T00:00:00.000000Z",
        "deck_type": "Limited",
        "game_type": "Two Player Duel",
        "total_turns": 2,
        "winner": "Alice",
        "players": [
            {
                "name": "Alice",
                "type": "pilot",
                "model": "model-a",
                "deck_name": "Deck A",
                "total_cost_usd": 0.0,
                "tool_calls_ok": 0,
                "tool_calls_failed": 0,
                "thinking_time_secs": 0.0,
            },
            {
                "name": "Bob",
                "type": "pilot",
                "model": "model-b",
                "deck_name": "Deck B",
                "total_cost_usd": 0.0,
                "tool_calls_ok": 0,
                "tool_calls_failed": 0,
                "thinking_time_secs": 0.0,
            },
        ],
        "card_images": {},
        "snapshots": [
            {
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "step": "PRECOMBAT_MAIN",
                "active_player": "Alice",
                "priority_player": "Alice",
                "seq": 5,
                "players": [
                    {
                        "name": "Alice",
                        "life": 20,
                        "library_size": 53,
                        "battlefield": [],
                        "graveyard": [],
                        "hand": [],
                    },
                    {
                        "name": "Bob",
                        "life": 20,
                        "library_size": 53,
                        "battlefield": [],
                        "graveyard": [],
                        "hand": [],
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
                "players": [
                    {
                        "name": "Alice",
                        "life": 20,
                        "library_size": 53,
                        "battlefield": [],
                        "graveyard": [],
                        "hand": [],
                    },
                    {
                        "name": "Bob",
                        "life": 20,
                        "library_size": 53,
                        "battlefield": [],
                        "graveyard": [],
                        "hand": [],
                    },
                ],
                "stack": [],
            },
        ],
        "actions": [],
        "llm_events": [
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
                "game_seq": 5,
            },
            {
                "type": "tool_call",
                "player": "Bob",
                "ts": "2026-03-01T00:00:03.000000Z",
                "tool": "get_action_choices",
                "args": {},
                "result": "{}",
                "game_seq": 10,
            },
        ],
        "game_over": None,
        "harness_epoch": 46,
        "youtube_url": "",
        "season": 1,
        "tournament": None,
    }
    if annotated:
        export["annotations"] = []
        export["blunder_script_version"] = 0
    path = tmp_path / "game_test.json5"
    path.write_text(json.dumps(export))
    return path


def test_find_context_uses_timestamp_for_older_exports() -> None:
    snapshots = [
        Snapshot(
            seq=5,
            turn=1,
            phase="PRECOMBAT_MAIN",
            step=None,
            active_player="Alice",
            priority_player=None,
            players=[],
            stack=[],
            ts="2026-03-01T00:00:05.000000Z",
        ),
        Snapshot(
            seq=10,
            turn=2,
            phase="PRECOMBAT_MAIN",
            step=None,
            active_player="Bob",
            priority_player=None,
            players=[],
            stack=[],
            ts="2026-03-01T00:00:10.000000Z",
        ),
    ]
    # Use a dataclass instance — GameStartEvent is arbitrary, we just need ts/game_seq
    event = GameStartEvent(type="game_start", player="Alice", ts="2026-03-01T00:00:06.000000Z")

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


def test_unannotated_exports_are_supported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    export_path = _write_export(tmp_path, annotated=False)

    with patch.object(sys, "argv", ["game_timeline.py", str(export_path)]):
        game_timeline.main()

    out = capsys.readouterr().out

    assert "Game: game_test" in out
    assert "Turns: 2 | Winner: Alice" in out
    assert "T1 PRECOMBAT_MAIN (Alice)" in out
    assert "T2 PRECOMBAT_MAIN (Bob)" in out
    assert "(3 events shown)" in out
