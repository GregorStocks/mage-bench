"""Tests for board cursor injection in the pilot loop."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from puppeteer.pilot import BoardCursorTracker, run_pilot_loop

# --- BoardCursorTracker unit tests ---


def test_tracker_inject_no_cursor_initially():
    t = BoardCursorTracker()
    args: dict = {}
    t.inject("pass_priority", args)
    assert "board_cursor" not in args


def test_tracker_inject_after_extract():
    t = BoardCursorTracker()
    t.extract('{"board_cursor": 5, "action_pending": true}')
    args: dict = {}
    t.inject("pass_priority", args)
    assert args["board_cursor"] == 5


def test_tracker_inject_skips_other_tools():
    t = BoardCursorTracker()
    t.extract('{"board_cursor": 5}')
    args: dict = {}
    t.inject("choose_action", args)
    assert "board_cursor" not in args
    t.inject("get_game_state", args)
    assert "board_cursor" not in args


def test_tracker_reset():
    t = BoardCursorTracker()
    t.extract('{"board_cursor": 5}')
    t.reset()
    args: dict = {}
    t.inject("pass_priority", args)
    assert "board_cursor" not in args


def test_tracker_extract_ignores_bad_json():
    t = BoardCursorTracker()
    t.extract("not json")
    assert t.cursor is None


def test_tracker_extract_ignores_missing_field():
    t = BoardCursorTracker()
    t.extract('{"action_pending": true}')
    assert t.cursor is None


# --- Helpers for pilot loop integration tests ---


def _mock_tool_result(text: str) -> MagicMock:
    result = MagicMock()
    result.content = [MagicMock(text=text)]
    return result


def _make_llm_response(tool_name: str, args: str) -> MagicMock:
    """Create a mock LLM response that requests a single tool call."""
    tool_call = MagicMock()
    tool_call.id = f"call_{id(tool_call)}"
    tool_call.function.name = tool_name
    tool_call.function.arguments = args

    choice = MagicMock()
    choice.finish_reason = "tool_calls"
    choice.message.tool_calls = [tool_call]
    choice.message.content = None

    response = MagicMock()
    response.choices = [choice]
    response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    return response


_TOOLS = [
    {"type": "function", "function": {"name": "pass_priority", "parameters": {}}},
    {"type": "function", "function": {"name": "get_action_choices", "parameters": {}}},
    {"type": "function", "function": {"name": "choose_action", "parameters": {}}},
]


@pytest.fixture()
def _no_prefetch():
    with patch(
        "puppeteer.pilot._prefetch_first_action",
        new_callable=AsyncMock,
        return_value="Game starting.",
    ):
        yield


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_board_cursor_injected_into_pass_priority():
    """After receiving a board_cursor, the pilot injects it into the next pass_priority call."""
    session = MagicMock()
    tool_calls: list[tuple[str, dict]] = []

    async def fake_call_tool(name: str, args: dict) -> MagicMock:
        tool_calls.append((name, dict(args)))
        if name == "pass_priority":
            # First pass_priority returns board_cursor=5
            if len(tool_calls) <= 2:
                return _mock_tool_result(
                    json.dumps(
                        {
                            "action_pending": True,
                            "action_type": "GAME_SELECT",
                            "stop_reason": "playable_cards",
                            "board": [{"name": "You", "life": 20}],
                            "board_cursor": 5,
                        }
                    )
                )
            # Second pass_priority should have board_cursor=5 injected
            return _mock_tool_result('{"game_over": true}')
        if name == "choose_action":
            return _mock_tool_result('{"success": true, "action_taken": "pass"}')
        return _mock_tool_result("{}")

    session.call_tool = AsyncMock(side_effect=fake_call_tool)

    # LLM turn 1: pass_priority → gets board_cursor=5
    # LLM turn 2: choose_action → pass
    # LLM turn 3: pass_priority → should have board_cursor=5 injected → game_over
    llm_responses = [
        _make_llm_response("pass_priority", "{}"),
        _make_llm_response("choose_action", '{"answer": false}'),
        _make_llm_response("pass_priority", "{}"),
    ]

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=llm_responses)

    with patch("puppeteer.pilot.auto_pass_loop", new_callable=AsyncMock):
        await run_pilot_loop(
            session=session,
            client=client,
            model="test-model",
            system_prompt="You are a test.",
            tools=_TOOLS,
            prices={},
            username="test-player",
        )

    # Find the second pass_priority call (after the first returned board_cursor=5)
    pass_calls = [(n, a) for n, a in tool_calls if n == "pass_priority"]
    assert len(pass_calls) >= 2
    # First call: no board_cursor (none known yet)
    assert "board_cursor" not in pass_calls[0][1]
    # Second call: board_cursor=5 injected
    assert pass_calls[1][1].get("board_cursor") == 5


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_board_cursor_injected_into_get_action_choices():
    """After receiving a board_cursor from pass_priority, the pilot injects it into get_action_choices."""
    session = MagicMock()
    tool_calls: list[tuple[str, dict]] = []

    async def fake_call_tool(name: str, args: dict) -> MagicMock:
        tool_calls.append((name, dict(args)))
        if name == "pass_priority":
            return _mock_tool_result(
                json.dumps(
                    {
                        "action_pending": True,
                        "action_type": "GAME_SELECT",
                        "board_cursor": 7,
                    }
                )
            )
        if name == "get_action_choices":
            return _mock_tool_result('{"action_pending": true, "game_over": true}')
        return _mock_tool_result("{}")

    session.call_tool = AsyncMock(side_effect=fake_call_tool)

    llm_responses = [
        _make_llm_response("pass_priority", "{}"),
        _make_llm_response("get_action_choices", "{}"),
    ]

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=llm_responses)

    with patch("puppeteer.pilot.auto_pass_loop", new_callable=AsyncMock):
        await run_pilot_loop(
            session=session,
            client=client,
            model="test-model",
            system_prompt="You are a test.",
            tools=_TOOLS,
            prices={},
            username="test-player",
        )

    gac_calls = [(n, a) for n, a in tool_calls if n == "get_action_choices"]
    assert len(gac_calls) >= 1
    assert gac_calls[0][1].get("board_cursor") == 7


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_board_cursor_updates_on_new_value():
    """The board_cursor should update when a new value is returned."""
    session = MagicMock()
    tool_calls: list[tuple[str, dict]] = []
    call_count = 0

    async def fake_call_tool(name: str, args: dict) -> MagicMock:
        nonlocal call_count
        tool_calls.append((name, dict(args)))
        call_count += 1
        if name == "pass_priority":
            if call_count <= 2:
                # First: board_cursor=3
                return _mock_tool_result(json.dumps({"action_pending": True, "board_cursor": 3}))
            if call_count <= 4:
                # After choose_action: board_cursor=4 (board changed)
                return _mock_tool_result(json.dumps({"action_pending": True, "board_cursor": 4}))
            return _mock_tool_result('{"game_over": true}')
        if name == "choose_action":
            return _mock_tool_result('{"success": true, "action_taken": "cast"}')
        return _mock_tool_result("{}")

    session.call_tool = AsyncMock(side_effect=fake_call_tool)

    llm_responses = [
        _make_llm_response("pass_priority", "{}"),
        _make_llm_response("choose_action", '{"index": 0}'),
        _make_llm_response("pass_priority", "{}"),
        _make_llm_response("choose_action", '{"index": 0}'),
        _make_llm_response("pass_priority", "{}"),
    ]

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=llm_responses)

    with patch("puppeteer.pilot.auto_pass_loop", new_callable=AsyncMock):
        await run_pilot_loop(
            session=session,
            client=client,
            model="test-model",
            system_prompt="You are a test.",
            tools=_TOOLS,
            prices={},
            username="test-player",
        )

    pass_calls = [(n, a) for n, a in tool_calls if n == "pass_priority"]
    assert len(pass_calls) >= 3
    # First: no cursor
    assert "board_cursor" not in pass_calls[0][1]
    # Second: cursor=3 (from first result)
    assert pass_calls[1][1].get("board_cursor") == 3
    # Third: cursor=4 (updated from second result)
    assert pass_calls[2][1].get("board_cursor") == 4


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_no_cursor_injected_for_other_tools():
    """Board cursor should NOT be injected into non-board tools like choose_action."""
    session = MagicMock()
    tool_calls: list[tuple[str, dict]] = []

    call_count = 0

    async def fake_call_tool(name: str, args: dict) -> MagicMock:
        nonlocal call_count
        tool_calls.append((name, dict(args)))
        call_count += 1
        if name == "pass_priority":
            # First pass_priority: action pending with board_cursor
            return _mock_tool_result(
                json.dumps({"action_pending": True, "action_type": "GAME_SELECT", "board_cursor": 10})
            )
        if name == "choose_action":
            # choose_action succeeds, then game over on next pass
            return _mock_tool_result('{"success": true, "action_taken": "pass", "game_over": true}')
        return _mock_tool_result("{}")

    session.call_tool = AsyncMock(side_effect=fake_call_tool)

    llm_responses = [
        _make_llm_response("pass_priority", "{}"),
        _make_llm_response("choose_action", '{"answer": false}'),
    ]

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=llm_responses)

    with patch("puppeteer.pilot.auto_pass_loop", new_callable=AsyncMock):
        await run_pilot_loop(
            session=session,
            client=client,
            model="test-model",
            system_prompt="You are a test.",
            tools=_TOOLS,
            prices={},
            username="test-player",
        )

    action_calls = [(n, a) for n, a in tool_calls if n == "choose_action"]
    assert len(action_calls) >= 1
    # choose_action should NOT have board_cursor injected
    assert "board_cursor" not in action_calls[0][1]
