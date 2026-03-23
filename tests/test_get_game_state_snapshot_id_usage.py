from pathlib import Path

from magebench.analysis.toolbox import get_game_state_snapshot_id_usage as usage


def test_analyze_games_counts_get_game_state_arguments(monkeypatch):
    fake_paths = [Path("game_a.json5"), Path("game_b.json5")]
    fake_exports = {
        fake_paths[0]: {
            "id": "game_a",
            "players": [
                {"name": "PilotA", "model": "model-a"},
            ],
            "llm_events": [
                {
                    "type": "llm_response",
                    "player": "PilotA",
                    "tool_calls": [
                        {"name": "get_game_state", "arguments": "{}"},
                        {"name": "get_game_state", "arguments": '{"cursor": 123}'},
                        {"name": "pass_priority", "arguments": "{}"},
                    ],
                },
            ],
        },
        fake_paths[1]: {
            "id": "game_b",
            "players": [
                {"name": "PilotB", "model": "model-b"},
            ],
            "llm_events": [
                {
                    "type": "llm_response",
                    "player": "PilotB",
                    "tool_calls": [
                        {"name": "get_game_state", "arguments": '{"snapshot_id": 77}'},
                    ],
                },
            ],
        },
    }

    monkeypatch.setattr(usage, "glob_game_export_paths", lambda _games_dir: fake_paths)
    monkeypatch.setattr(usage, "load_raw_game_export", lambda path: fake_exports[path])

    report = usage.analyze_games(Path("unused"))

    assert report.files_scanned == 2
    assert report.llm_response_events_scanned == 2
    assert report.get_game_state_calls == 3
    assert report.get_game_state_calls_with_any_args == 2
    assert report.get_game_state_calls_with_cursor == 1
    assert report.get_game_state_calls_with_snapshot_id == 1
    assert report.argument_key_counts == {"cursor": 1, "snapshot_id": 1}
    assert [example.game_id for example in report.examples_with_args] == ["game_a", "game_b"]
