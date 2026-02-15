"""Tests for the pilot module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from puppeteer.pilot import (
    PermanentLLMFailure,
    _prefetch_first_action,
    mcp_tools_to_openai,
    run_pilot_loop,
)


def _make_session() -> MagicMock:
    """Create a mock MCP session."""
    session = MagicMock()
    result = MagicMock()
    result.content = [MagicMock(text='{"ok": true}')]
    session.call_tool = AsyncMock(return_value=result)
    return session


def _make_client(error: Exception) -> MagicMock:
    """Create a mock OpenAI client whose chat.completions.create raises *error*."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=error)
    return client


@pytest.fixture()
def _no_prefetch():
    """Patch _prefetch_first_action so run_pilot_loop tests don't block."""
    with patch("puppeteer.pilot._prefetch_first_action", new_callable=AsyncMock, return_value="Game starting."):
        yield


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_403_raises_permanent_failure():
    """A 403 error (key quota exceeded) should raise PermanentLLMFailure."""
    session = _make_session()
    client = _make_client(Exception("Error code: 403 - Forbidden"))

    with pytest.raises(PermanentLLMFailure, match="Credits exhausted"):
        await run_pilot_loop(
            session=session,
            client=client,
            model="test-model",
            system_prompt="You are a test.",
            tools=[],
            username="test-player",
        )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_402_raises_permanent_failure():
    """A 402 error (credits exhausted) should raise PermanentLLMFailure."""
    session = _make_session()
    client = _make_client(Exception("Error code: 402 - Payment Required"))

    with pytest.raises(PermanentLLMFailure, match="Credits exhausted"):
        await run_pilot_loop(
            session=session,
            client=client,
            model="test-model",
            system_prompt="You are a test.",
            tools=[],
            username="test-player",
        )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_404_raises_permanent_failure():
    """A 404 error (model not found) should raise PermanentLLMFailure."""
    session = _make_session()
    client = _make_client(Exception("Error code: 404 - Not Found"))

    with pytest.raises(PermanentLLMFailure, match="Model not found"):
        await run_pilot_loop(
            session=session,
            client=client,
            model="test-model",
            system_prompt="You are a test.",
            tools=[],
            username="test-player",
        )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_game_over_from_pass_priority_triggers_auto_pass():
    """When pass_priority returns game_over, pilot should switch to auto-pass."""
    session = _make_session()

    # Mock pass_priority to return game_over
    pass_result = MagicMock()
    pass_result.content = [MagicMock(text='{"game_over": true, "timeout": true}')]
    session.call_tool = AsyncMock(return_value=pass_result)

    # Mock LLM to call pass_priority
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "pass_priority"
    tool_call.function.arguments = '{"timeout_ms": 10000}'

    choice = MagicMock()
    choice.finish_reason = "tool_calls"
    choice.message.tool_calls = [tool_call]
    choice.message.content = None

    response = MagicMock()
    response.choices = [choice]
    response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)

    with patch("puppeteer.pilot.auto_pass_loop", new_callable=AsyncMock) as mock_auto_pass:
        await run_pilot_loop(
            session=session,
            client=client,
            model="test-model",
            system_prompt="You are a test.",
            tools=[{"type": "function", "function": {"name": "pass_priority", "parameters": {}}}],
            username="test-player",
        )
        mock_auto_pass.assert_called_once()


# --- mcp_tools_to_openai tests ---


def _make_mcp_tool(name: str) -> MagicMock:
    """Create a mock MCP tool definition."""
    tool = MagicMock()
    tool.name = name
    tool.description = f"Description for {name}"
    tool.inputSchema = {"type": "object", "properties": {}}
    return tool


def test_mcp_tools_to_openai_no_filter():
    """With no allowed_tools, should include all MCP tools."""
    mcp_tools = [_make_mcp_tool(name) for name in ["pass_priority", "choose_action", "wait_for_action"]]
    result = mcp_tools_to_openai(mcp_tools)
    names = {t["function"]["name"] for t in result}
    assert names == {"pass_priority", "choose_action", "wait_for_action"}


def test_mcp_tools_to_openai_custom_filter():
    """With custom allowed_tools, should filter to that set."""
    mcp_tools = [_make_mcp_tool(name) for name in ["pass_priority", "choose_action", "get_game_state"]]
    custom = {"pass_priority", "get_game_state"}
    result = mcp_tools_to_openai(mcp_tools, allowed_tools=custom)
    names = {t["function"]["name"] for t in result}
    assert names == {"pass_priority", "get_game_state"}
    assert "choose_action" not in names


# --- _prefetch_first_action tests ---


def _mock_tool_result(text: str) -> MagicMock:
    result = MagicMock()
    result.content = [MagicMock(text=text)]
    return result


@pytest.mark.asyncio
async def test_prefetch_mulligan():
    """Pre-fetch should detect mulligan from pass_priority inline choices."""
    session = MagicMock()

    async def fake_call_tool(name, args):
        if name == "pass_priority":
            # pass_priority returns choices inline (including message)
            return _mock_tool_result(
                '{"action_pending": true, "action_type": "GAME_ASK", '
                '"message": "Mulligan down to 6 cards?", "choices": []}'
            )
        raise AssertionError(f"Unexpected tool: {name}")

    session.call_tool = AsyncMock(side_effect=fake_call_tool)
    msg = await _prefetch_first_action(session)
    assert "Mulligan" in msg
    assert "choose_action" in msg


@pytest.mark.asyncio
async def test_prefetch_waits_for_action():
    """Pre-fetch calls pass_priority once (it blocks) and uses inline choices."""
    session = MagicMock()
    calls = []

    async def fake_call_tool(name, args):
        calls.append(name)
        if name == "pass_priority":
            # pass_priority returns choices inline
            return _mock_tool_result(
                '{"action_pending": true, "action_type": "GAME_ASK", "message": "Choose play or draw"}'
            )
        raise AssertionError(f"Unexpected tool: {name}")

    session.call_tool = AsyncMock(side_effect=fake_call_tool)
    msg = await _prefetch_first_action(session)
    assert "GAME_ASK" in msg
    # Single blocking call, no separate get_action_choices
    assert calls == ["pass_priority"]


@pytest.mark.asyncio
async def test_prefetch_game_over():
    """If pass_priority returns game_over, return a game-over message."""
    session = MagicMock()
    session.call_tool = AsyncMock(return_value=_mock_tool_result('{"game_over": true}'))
    msg = await _prefetch_first_action(session)
    assert "over" in msg.lower()


# --- consecutive pass_priority error tests ---


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


_BAD_PASS_ARGS = '{"until":"invalid_value"}'
_PASS_ERROR = '{"error": "Invalid until value: invalid_value"}'
_PASS_OK = '{"action_pending": false, "stop_reason": "passed"}'
_TOOLS = [{"type": "function", "function": {"name": "pass_priority", "parameters": {}}}]


@pytest.mark.asyncio
async def test_repeated_pass_error_forces_plain_pass():
    """After 3 identical pass_priority errors, pilot forces a plain pass."""
    session = MagicMock()
    tool_calls = []
    pass_call_count = 0

    async def fake_call_tool(name, args):
        nonlocal pass_call_count
        tool_calls.append((name, dict(args) if args else {}))
        if name == "pass_priority":
            pass_call_count += 1
            if pass_call_count == 1:  # prefetch
                return _mock_tool_result('{"action_pending": true, "action_type": "GAME_SELECT"}')
            if pass_call_count in (2, 3, 4):  # LLM turns 1-3: error
                return _mock_tool_result(_PASS_ERROR)
            if pass_call_count == 5:  # forced plain pass
                return _mock_tool_result(_PASS_OK)
            return _mock_tool_result('{"game_over": true}')  # exit
        if name == "get_action_choices":
            return _mock_tool_result('{"action_type": "GAME_SELECT", "message": "Choose"}')
        return _mock_tool_result("{}")

    session.call_tool = AsyncMock(side_effect=fake_call_tool)

    bad_pass = _make_llm_response("pass_priority", _BAD_PASS_ARGS)
    clean_pass = _make_llm_response("pass_priority", "{}")

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=[bad_pass, bad_pass, bad_pass, clean_pass])

    with patch("puppeteer.pilot.auto_pass_loop", new_callable=AsyncMock):
        await run_pilot_loop(
            session=session,
            client=client,
            model="test-model",
            system_prompt="You are a test.",
            tools=_TOOLS,
            username="test-player",
        )

    # Verify the forced plain pass was called (pass_priority with empty args)
    pass_calls = [(n, a) for n, a in tool_calls if n == "pass_priority"]
    # pass_calls[0] = prefetch ({}), [1-3] = LLM errors (bad args), [4] = forced ({})
    assert len(pass_calls) >= 5
    assert pass_calls[4] == ("pass_priority", {})


@pytest.mark.asyncio
async def test_different_pass_errors_dont_trigger_forced_pass():
    """Alternating between different errors should not trigger forced pass."""
    session = MagicMock()
    forced_pass_count = 0
    pass_call_count = 0
    error_a = '{"error": "error A"}'
    error_b = '{"error": "error B"}'

    async def fake_call_tool(name, args):
        nonlocal pass_call_count, forced_pass_count
        if name == "pass_priority":
            pass_call_count += 1
            # Detect forced passes: they come with empty args after an error sequence
            if not args or args == {}:
                if pass_call_count > 1:  # not prefetch
                    forced_pass_count += 1
            if pass_call_count == 1:  # prefetch
                return _mock_tool_result('{"action_pending": true, "action_type": "GAME_SELECT"}')
            if pass_call_count > 5:
                return _mock_tool_result('{"game_over": true}')
            # Alternate between two different errors
            if pass_call_count % 2 == 0:
                return _mock_tool_result(error_a)
            return _mock_tool_result(error_b)
        if name == "get_action_choices":
            return _mock_tool_result('{"action_type": "GAME_SELECT", "message": "Choose"}')
        return _mock_tool_result("{}")

    session.call_tool = AsyncMock(side_effect=fake_call_tool)

    bad_pass = _make_llm_response("pass_priority", '{"until":"bad"}')
    # Use until for the exit call so it's distinguishable from forced {}
    exit_pass = _make_llm_response("pass_priority", '{"until":"my_turn"}')

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=[bad_pass, bad_pass, bad_pass, bad_pass, exit_pass])

    with patch("puppeteer.pilot.auto_pass_loop", new_callable=AsyncMock):
        await run_pilot_loop(
            session=session,
            client=client,
            model="test-model",
            system_prompt="You are a test.",
            tools=_TOOLS,
            username="test-player",
        )

    # No forced plain pass should have been made (errors alternate, never 3 identical)
    assert forced_pass_count == 0


@pytest.mark.asyncio
async def test_successful_pass_resets_error_counter():
    """A successful pass between errors should reset the consecutive counter."""
    session = MagicMock()
    forced_pass_count = 0
    pass_call_count = 0

    async def fake_call_tool(name, args):
        nonlocal pass_call_count, forced_pass_count
        if name == "pass_priority":
            pass_call_count += 1
            if not args or args == {}:
                if pass_call_count > 1:  # not prefetch
                    forced_pass_count += 1
            if pass_call_count == 1:  # prefetch
                return _mock_tool_result('{"action_pending": true, "action_type": "GAME_SELECT"}')
            if pass_call_count in (2, 3):  # 2 errors
                return _mock_tool_result(_PASS_ERROR)
            if pass_call_count == 4:  # success resets counter
                return _mock_tool_result('{"action_pending": true, "stop_reason": "playable_cards"}')
            if pass_call_count in (5, 6):  # 2 more errors (not 3 consecutive)
                return _mock_tool_result(_PASS_ERROR)
            return _mock_tool_result('{"game_over": true}')
        if name == "get_action_choices":
            return _mock_tool_result('{"action_type": "GAME_SELECT", "message": "Choose"}')
        return _mock_tool_result("{}")

    session.call_tool = AsyncMock(side_effect=fake_call_tool)

    bad_pass = _make_llm_response("pass_priority", _BAD_PASS_ARGS)
    # Use until for the "ok" calls so they're distinguishable from forced {}
    ok_pass = _make_llm_response("pass_priority", '{"until":"my_turn"}')

    client = MagicMock()
    # 2 errors, 1 success, 2 errors, then game_over
    client.chat.completions.create = AsyncMock(side_effect=[bad_pass, bad_pass, ok_pass, bad_pass, bad_pass, ok_pass])

    with patch("puppeteer.pilot.auto_pass_loop", new_callable=AsyncMock):
        await run_pilot_loop(
            session=session,
            client=client,
            model="test-model",
            system_prompt="You are a test.",
            tools=_TOOLS,
            username="test-player",
        )

    # No forced plain pass should have been made (never hit 3 consecutive)
    assert forced_pass_count == 0
