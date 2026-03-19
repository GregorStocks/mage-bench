import asyncio
import json
from pathlib import Path

import pytest

import tests.golden_helpers as golden_helpers
from schemas.game_export_types import Choice, CombatCreature, Permanent, StackItem, StackTarget
from scripts.json5_utils import dumps_json5, loads_json5
from tests.golden_helpers import (
    _CapturedPilotRequest,
    _json_diff,
    _json_ready,
    _normalize_embedded_json,
    _normalize_prompt_for_golden,
    _pilot_script_from_replay_script,
    _ScriptedChatCompletions,
    _ScriptedExecutionState,
    _strip_volatile,
    extract_blunder_decisions,
)


def test_normalize_prompt_preserves_local_short_ids():
    payload = [{"content": '{"id":"l5","name":"Lotus Petal"}'}]

    normalized = _normalize_prompt_for_golden(payload)
    assert normalized[0]["content"]["id"] == "l5"


def test_normalize_prompt_preserves_choice_short_ids():
    payload = [{"content": '{"choice":"p11","name":"Lightning Bolt"}'}]

    normalized = _normalize_prompt_for_golden(payload)
    assert normalized[0]["content"]["choice"] == "p11"


def test_normalize_prompt_preserves_choice_non_ids():
    """Non-ID choice values (integers, booleans) should not be normalized."""
    # "0" is valid JSON for integer 0, so it gets parsed to int
    payload = [{"content": '{"choice":"0"}'}]
    normalized = _normalize_prompt_for_golden(payload)
    assert normalized[0]["content"]["choice"] == 0

    payload2 = [{"content": '{"choice":"yes"}'}]
    normalized2 = _normalize_prompt_for_golden(payload2)
    assert normalized2[0]["content"]["choice"] == "yes"


def test_normalize_embedded_json_sorts_keys():
    payload = {"result": '{"b":2,"a":1}'}

    normalized = _normalize_embedded_json(payload)
    assert normalized == {"result": {"a": 1, "b": 2}}


def test_normalize_embedded_json_preserves_short_ids():
    payload = {"result": '{"players":[{"id":"p3"},{"id":"p11"}]}'}

    normalized = _normalize_embedded_json(payload)
    assert normalized["result"] == {"players": [{"id": "p3"}, {"id": "p11"}]}


def test_normalize_embedded_json_handles_nested_json_strings():
    payload = {"result": '{"outer":"{\\"id\\":\\"p9\\",\\"k\\":2}","id":"p1"}'}

    normalized = _normalize_embedded_json(payload)

    assert normalized["result"]["id"] == "p1"
    assert normalized["result"]["outer"] == {"id": "p9", "k": 2}


def test_normalize_embedded_json_converts_dataclass_export_records():
    payload = {
        "snapshots": [
            {
                "players": [
                    {
                        "battlefield": [
                            Permanent(
                                name="Mountain",
                                id="p3",
                                _extras={"visible_to": ["Opponent"]},
                            )
                        ]
                    }
                ],
                "stack": [
                    StackItem(
                        name="Lightning Bolt",
                        _extras={"controller": "Alice"},
                        targets=[StackTarget(name="Goblin Guide", id="p1")],
                    )
                ],
                "combat": [{"attackers": [CombatCreature(name="Goblin Guide", id="a1")]}],
            }
        ]
    }

    normalized = _normalize_embedded_json(payload)

    assert normalized == {
        "snapshots": [
            {
                "players": [
                    {
                        "battlefield": [
                            {
                                "name": "Mountain",
                                "id": "p3",
                                "visible_to": ["Opponent"],
                            }
                        ]
                    }
                ],
                "stack": [
                    {
                        "name": "Lightning Bolt",
                        "controller": "Alice",
                        "targets": [{"name": "Goblin Guide", "id": "p1"}],
                    }
                ],
                "combat": [{"attackers": [{"name": "Goblin Guide", "id": "a1"}]}],
            }
        ]
    }


def test_dumps_json5_serializes_dataclass_export_records():
    payload = {
        "battlefield": [
            Permanent(
                name="Mountain",
                id="p3",
                _extras={"visible_to": ["Opponent"], "mana_cost": "{R}"},
            )
        ]
    }

    parsed = loads_json5(dumps_json5(_json_ready(payload), sort_keys=True))

    assert parsed == {
        "battlefield": [
            {
                "id": "p3",
                "mana_cost": "{R}",
                "name": "Mountain",
                "visible_to": ["Opponent"],
            }
        ]
    }


def test_extract_blunder_decisions_serializes_dataclass_export_records(tmp_path: Path, monkeypatch):
    captured: dict[str, object] = {}

    def fake_extract_decisions(path: str) -> list[dict]:
        captured["payload"] = json.loads(Path(path).read_text())
        return []

    monkeypatch.setattr("tests.golden_helpers.extract_decisions", fake_extract_decisions)

    export_data = {
        "snapshots": [
            {
                "players": [
                    {
                        "battlefield": [
                            Permanent(
                                name="Mountain",
                                id="p3",
                                _extras={"visible_to": ["Opponent"]},
                            )
                        ],
                        "graveyard": [],
                        "hand": [],
                    }
                ],
                "stack": [],
            }
        ]
    }

    assert extract_blunder_decisions(export_data, tmp_path) == []
    assert captured["payload"] == {
        "snapshots": [
            {
                "players": [
                    {
                        "battlefield": [
                            {
                                "id": "p3",
                                "name": "Mountain",
                                "visible_to": ["Opponent"],
                            }
                        ],
                        "graveyard": [],
                        "hand": [],
                    }
                ],
                "stack": [],
            }
        ]
    }
    assert not (tmp_path / "game_blunder_export.json").exists()


def test_normalize_embedded_json_preserves_non_json_strings():
    payload = {
        "description": (
            "<font color='#F0E68C' object_id='12345678-1234-1234-1234-123456789abc'>"
            "Savannah Lions</font> [7e2], P/T: 2/1"
        )
    }

    normalized = _normalize_embedded_json(payload)

    assert normalized["description"] == payload["description"]


def test_normalize_prompt_preserves_game_seq():
    payload = [{"content": '{"game_seq":77,"id":"p3","nested":{"game_seq":12}}'}]

    normalized = _normalize_prompt_for_golden(payload)

    assert normalized[0]["content"] == {"game_seq": 77, "id": "p3", "nested": {"game_seq": 12}}


def test_pilot_script_from_replay_script_drops_initial_prefetch_call():
    script = [
        {"name": "pass_priority", "arguments": {}},
        {"name": "choose_action", "arguments": {"choice": "0"}},
        {"name": "get_game_state", "arguments": {}},
    ]

    assert _pilot_script_from_replay_script(script) == script[1:]


def test_pilot_script_from_replay_script_requires_initial_plain_pass_priority():
    with pytest.raises(AssertionError, match="must start with pass_priority"):
        _pilot_script_from_replay_script([{"name": "get_game_state", "arguments": {}}])

    with pytest.raises(AssertionError, match="pass_priority\\(\\{\\}\\)"):
        _pilot_script_from_replay_script([{"name": "pass_priority", "arguments": {"until": "my_turn"}}])


def test_pilot_script_from_replay_script_filters_assert_action_steps():
    script = [
        {"name": "pass_priority", "arguments": {}},
        {"name": "assert_action", "arguments": {"action_type": "GAME_SELECT"}},
        {"name": "choose_action", "arguments": {"choice": "0"}},
        {"name": "assert_action", "arguments": {"action_type": "GAME_ASK"}},
        {"name": "get_game_state", "arguments": {}},
    ]

    assert _pilot_script_from_replay_script(script) == [
        {"name": "choose_action", "arguments": {"choice": "0"}},
        {"name": "get_game_state", "arguments": {}},
    ]


@pytest.mark.asyncio
async def test_scripted_chat_completion_captures_terminal_request():
    capture = _CapturedPilotRequest()
    completions = _ScriptedChatCompletions([{"name": "choose_action", "arguments": {"choice": "0"}}], capture)

    first_messages = [{"role": "user", "content": "before scripted tool call"}]
    second_messages = [{"role": "user", "content": "after scripted tool call"}]

    response = await completions.create(messages=first_messages)
    assert response.choices[0].message.tool_calls[0].function.name == "choose_action"
    assert capture.last_messages == first_messages
    assert capture.post_script_messages is None

    with pytest.raises(asyncio.CancelledError):
        await completions.create(messages=second_messages)

    assert capture.last_messages == second_messages
    assert capture.post_script_messages == second_messages


@pytest.mark.asyncio
async def test_scripted_chat_completion_skips_assert_action_steps():
    capture = _CapturedPilotRequest()
    execution_state = _ScriptedExecutionState(
        last_tool_name="pass_priority",
        last_result_text=(
            '{"action_pending": true, "action_type": "GAME_GET_MULTI_AMOUNT", "response_type": "multi_amount"}'
        ),
    )
    completions = _ScriptedChatCompletions(
        [
            {
                "name": "assert_action",
                "arguments": {"action_type": "GAME_GET_MULTI_AMOUNT", "response_type": "multi_amount"},
            },
            {"name": "choose_action", "arguments": {"amounts": [1, 1]}},
        ],
        capture,
        execution_state,
    )

    response = await completions.create(messages=[{"role": "user", "content": "before scripted tool call"}])

    assert response.choices[0].message.tool_calls[0].function.name == "choose_action"
    assert response.choices[0].message.tool_calls[0].function.arguments == '{"amounts": [1, 1]}'


def test_strip_volatile_sorts_llm_events_by_seq_player():
    data = {
        "llmEvents": [
            {"ts": "2025-01-01T00:00:00.000002", "latencyMs": 17, "player": "B", "seq": 2, "type": "llm_response"},
            {"ts": "2025-01-01T00:00:00.000003", "latencyMs": 9, "player": "A", "seq": 2, "type": "llm_response"},
            {"ts": "2025-01-01T00:00:00.000001", "latencyMs": 44, "player": "A", "seq": 1, "type": "game_start"},
            {"ts": "2025-01-01T00:00:00.000000", "latencyMs": 3, "player": "B", "seq": 1, "type": "game_start"},
        ],
        "llmTrace": [
            {"ts": "2025-01-01T00:00:00.000001", "player": "B", "seq": 1},
            {"ts": "2025-01-01T00:00:00.000000", "player": "A", "seq": 1},
        ],
    }

    _strip_volatile(data)

    # Sorted by (seq, player); wall-clock timing stripped before sorting.
    events = data["llmEvents"]
    assert all("ts" not in e for e in events)
    assert all("latencyMs" not in e for e in events)
    assert [(e["player"], e["seq"]) for e in events] == [
        ("A", 1),
        ("B", 1),
        ("A", 2),
        ("B", 2),
    ]

    trace = data["llmTrace"]
    assert all("ts" not in e for e in trace)
    assert [(e["player"], e["seq"]) for e in trace] == [("A", 1), ("B", 1)]


def test_strip_volatile_keeps_errors_but_strips_error_timestamps():
    data = {
        "timestamp": "2026-03-14T19:00:00Z",
        "id": "game_20260314_190000",
        "errors": [
            {
                "ts": "10:30:45",
                "player": "Alice",
                "source": "mcp",
                "message": "Zombie game detected: no actionable callback for 15000ms",
            },
            {
                "ts": "",
                "player": "Bob",
                "source": "unknown",
                "message": "no timestamp here",
            },
        ],
    }

    _strip_volatile(data)

    assert "timestamp" not in data
    assert data["id"] == "game_20260314_190000"
    assert data["errors"] == [
        {
            "player": "Alice",
            "source": "mcp",
            "message": "Zombie game detected: no actionable callback for 15000ms",
        },
        {
            "player": "Bob",
            "source": "unknown",
            "message": "no timestamp here",
        },
    ]


def test_extract_blunder_decisions_serializes_dataclass_leaves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    export_data = {
        "version": 8,
        "id": "game_20260317_000000",
        "timestamp": "",
        "gameType": "Two Player Duel",
        "deckType": "Constructed - Standard",
        "totalTurns": 1,
        "winner": None,
        "harnessEpoch": 0,
        "youtubeUrl": "",
        "players": [],
        "cardImages": {},
        "snapshots": [],
        "actions": [],
        "llmEvents": [],
        "gameOver": None,
        "season": 1,
        "tournament": None,
        "annotations": [],
        "blunderScriptVersion": 0,
        "decisions": [
            {
                "index": 0,
                "snapshotIndex": 0,
                "player": "Alice",
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "actionType": "play",
                "responseType": "choice",
                "message": "Play spells and abilities",
                "choices": [Choice.from_mapping({"index": 0, "name": "Memnite"})],
                "choiceCount": 1,
                "isForced": True,
                "llmEventIndices": [],
                "subsequentActions": [],
            }
        ],
    }

    def _fake_extract_decisions(path: str) -> list[dict]:
        payload = json.loads(Path(path).read_text())
        assert payload["decisions"][0]["choices"][0] == {"index": 0, "name": "Memnite"}
        return [{"index": 0}]

    monkeypatch.setattr(golden_helpers, "extract_decisions", _fake_extract_decisions)

    assert extract_blunder_decisions(export_data, tmp_path) == [{"index": 0}]


# --- _json_diff tests ---


def test_json_diff_identical():
    assert _json_diff({"a": 1}, {"a": 1}) == []


def test_json_diff_scalar_change():
    diffs = _json_diff({"name": "Bolt"}, {"name": "Strike"})
    assert diffs == ["  name: 'Bolt' -> 'Strike'"]


def test_json_diff_dict_key_added():
    diffs = _json_diff({"a": 1}, {"a": 1, "b": 2})
    assert diffs == ["  b: + 2"]


def test_json_diff_dict_key_removed():
    diffs = _json_diff({"a": 1, "b": 2}, {"a": 1})
    assert diffs == ["  b: - 2"]


def test_json_diff_list_element_changed():
    diffs = _json_diff([1, 2, 3], [1, 99, 3])
    assert diffs == ["  [1]: 2 -> 99"]


def test_json_diff_list_element_added():
    diffs = _json_diff([1, 2], [1, 2, 3])
    assert len(diffs) == 2
    assert "2 items -> 3 items" in diffs[0]
    assert "[2]: + 3" in diffs[1]


def test_json_diff_list_element_removed():
    diffs = _json_diff([1, 2, 3], [1, 2])
    assert len(diffs) == 2
    assert "3 items -> 2 items" in diffs[0]
    assert "[2]: - 3" in diffs[1]


def test_json_diff_nested_path():
    exp = {"decisions": [{"choices": [{"name": "Bolt"}]}]}
    act = {"decisions": [{"choices": [{"name": "Strike"}]}]}
    diffs = _json_diff(exp, act)
    assert diffs == ["  decisions[0].choices[0].name: 'Bolt' -> 'Strike'"]


def test_json_diff_type_mismatch():
    diffs = _json_diff({"x": 1}, {"x": "one"})
    assert diffs == ["  x: 1 -> 'one'"]


def test_json_diff_truncates_long_strings():
    long_str = "a" * 200
    diffs = _json_diff({"s": long_str}, {"s": "short"})
    assert len(diffs) == 1
    # The long string repr should be truncated
    assert "..." in diffs[0]


def test_json_diff_max_diffs():
    exp = {f"k{i}": i for i in range(50)}
    act = {f"k{i}": i + 1 for i in range(50)}
    diffs = _json_diff(exp, act, max_diffs=5)
    # 5 actual diffs + 1 truncation message
    assert len(diffs) == 6
    assert "truncated" in diffs[-1]
