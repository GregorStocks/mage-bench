"""Tests for the pilot module."""

import asyncio
import json
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import CallToolResult, TextContent
from openai import OpenAIError

from puppeteer.pilot import (
    MAX_CHAT_MESSAGES_PER_TURN,
    MAX_CONSECUTIVE_EMPTY_CHOICES,
    MAX_TOKENS,
    PermanentLLMError,
    _prefetch_first_action,
    main,
    run_pilot_loop,
)
from puppeteer.pilot_bridge import build_pilot_decision, build_pilot_snapshot, execute_tool, mcp_tools_to_openai
from puppeteer.pilot_game_state import extract_oracle_texts_from_board
from puppeteer.pilot_rendering import _fetch_state_summary, render_for_pilot
from puppeteer.tool_error import ToolExecutionError
from schemas.game_export_types import Decision, PilotContext


def _make_session() -> MagicMock:
    """Create a mock MCP session."""
    session = MagicMock()
    result = CallToolResult(content=[TextContent(type="text", text='{"ok": true}')])
    session.call_tool = AsyncMock(return_value=result)
    return session


def _make_client(error: Exception) -> MagicMock:
    """Create a mock OpenAI client whose chat.completions.create raises *error*."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=error)
    return client


@pytest.mark.asyncio
async def test_execute_tool_raises_on_mcp_failure():
    session = MagicMock()
    session.call_tool = AsyncMock(side_effect=RuntimeError("bridge died"))

    with pytest.raises(ToolExecutionError, match="MCP tool pass_priority failed: bridge died"):
        await execute_tool(session, "pass_priority", {})


@pytest.mark.asyncio
async def test_fetch_state_summary_raises_on_error_payload():
    session = MagicMock()
    result = CallToolResult(content=[TextContent(type="text", text='{"error": "bridge died"}')])
    session.call_tool = AsyncMock(return_value=result)

    with pytest.raises(ToolExecutionError, match="get_game_state returned error: bridge died"):
        await _fetch_state_summary(session)


def test_main_accepts_explicit_api_key_for_non_default_provider():
    def fake_run_pilot(*_args, **_kwargs):
        return "run-pilot-sentinel"

    with (
        patch.object(
            sys,
            "argv",
            [
                "pilot",
                "--api-key",
                "sk-test",
                "--provider",
                "openai",
            ],
        ),
        patch("puppeteer.pilot.setup_logging"),
        patch("puppeteer.pilot.load_prices", return_value={}),
        patch("puppeteer.pilot._load_default_system_prompt", return_value="system"),
        patch("puppeteer.pilot.run_pilot", new=fake_run_pilot),
        patch("puppeteer.pilot.asyncio.run") as run_mock,
    ):
        assert main() == 0

    run_mock.assert_called_once()


def test_main_reports_provider_in_missing_key_log(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.ERROR)

    with (
        patch.object(
            sys,
            "argv",
            [
                "pilot",
                "--provider",
                "openai",
            ],
        ),
        patch("puppeteer.pilot.setup_logging"),
        patch.dict("os.environ", {}, clear=True),
    ):
        assert main() == 2

    assert "Missing API key for provider openai" in caplog.text
    assert "configured API key env var" in caplog.text


@pytest.fixture
def _no_prefetch():
    """Patch _prefetch_first_action so run_pilot_loop tests don't block."""
    with patch("puppeteer.pilot._prefetch_first_action", new_callable=AsyncMock, return_value="Game starting."):
        yield


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_401_raises_permanent_failure():
    """A 401 error (user not found / bad API key) should raise PermanentLLMError."""
    session = _make_session()
    client = _make_client(OpenAIError("Error code: 401 - {'error': {'message': 'User not found.', 'code': 401}}"))

    with pytest.raises(PermanentLLMError, match="Credits exhausted"):
        await run_pilot_loop(
            session=session,
            client=client,
            model="test-model",
            system_prompt="You are a test.",
            tools=[],
            prices={},
            username="test-player",
        )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_403_raises_permanent_failure():
    """A 403 error (key quota exceeded) should raise PermanentLLMError."""
    session = _make_session()
    client = _make_client(OpenAIError("Error code: 403 - Forbidden"))

    with pytest.raises(PermanentLLMError, match="Credits exhausted"):
        await run_pilot_loop(
            session=session,
            client=client,
            model="test-model",
            system_prompt="You are a test.",
            tools=[],
            prices={},
            username="test-player",
        )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_402_raises_permanent_failure():
    """A 402 error (credits exhausted) should raise PermanentLLMError."""
    session = _make_session()
    client = _make_client(OpenAIError("Error code: 402 - Payment Required"))

    with pytest.raises(PermanentLLMError, match="Credits exhausted"):
        await run_pilot_loop(
            session=session,
            client=client,
            model="test-model",
            system_prompt="You are a test.",
            tools=[],
            prices={},
            username="test-player",
        )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_404_raises_permanent_failure():
    """A 404 error (model not found) should raise PermanentLLMError."""
    session = _make_session()
    client = _make_client(OpenAIError("Error code: 404 - Not Found"))

    with pytest.raises(PermanentLLMError, match="Model not found"):
        await run_pilot_loop(
            session=session,
            client=client,
            model="test-model",
            system_prompt="You are a test.",
            tools=[],
            prices={},
            username="test-player",
        )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_game_over_from_pass_priority_triggers_auto_pass():
    """When pass_priority returns game_over, pilot should switch to auto-pass."""
    session = _make_session()

    # Mock pass_priority to return game_over
    pass_result = CallToolResult(content=[TextContent(type="text", text='{"game_over": true, "timeout": true}')])
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
            prices={},
            username="test-player",
        )
        mock_auto_pass.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_game_over_from_get_action_choices_triggers_auto_pass():
    """When get_action_choices returns game_over, pilot should switch to auto-pass."""
    session = _make_session()

    # Mock get_action_choices to return game_over
    choices_text = '{"action_pending": false, "game_over": true}'
    choices_result = CallToolResult(content=[TextContent(type="text", text=choices_text)])
    session.call_tool = AsyncMock(return_value=choices_result)

    # Mock LLM to call get_action_choices
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "get_action_choices"
    tool_call.function.arguments = "{}"

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
            tools=[{"type": "function", "function": {"name": "get_action_choices", "parameters": {}}}],
            prices={},
            username="test-player",
        )
        mock_auto_pass.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_game_over_from_choose_action_triggers_auto_pass():
    """When choose_action returns game_over, pilot should switch to auto-pass."""
    session = _make_session()

    # Mock choose_action to return error with game_over
    result_json = (
        '{"success": false, "error": "No pending action after 10s wait",'
        ' "error_code": "no_pending_action", "game_over": true}'
    )
    action_result = CallToolResult(content=[TextContent(type="text", text=result_json)])
    session.call_tool = AsyncMock(return_value=action_result)

    # Mock LLM to call choose_action
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "choose_action"
    tool_call.function.arguments = '{"index": 0}'

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
            tools=[{"type": "function", "function": {"name": "choose_action", "parameters": {}}}],
            prices={},
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


def _mock_tool_result(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)])


@pytest.mark.asyncio
async def test_prefetch_mulligan():
    """Pre-fetch should detect mulligan from pass_priority inline choices."""
    session = MagicMock()

    async def fake_call_tool(name, _args):
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

    async def fake_call_tool(name, _args):
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
    response.usage.prompt_tokens_details = None
    response.usage.completion_tokens_details = None
    return response


def _make_truncated_response() -> MagicMock:
    """Create a mock LLM response truncated before it could emit tools."""
    choice = MagicMock()
    choice.finish_reason = "length"
    choice.message.tool_calls = None
    choice.message.content = None

    response = MagicMock()
    response.choices = [choice]
    response.usage = MagicMock(prompt_tokens=10, completion_tokens=MAX_TOKENS)
    response.usage.prompt_tokens_details = None
    response.usage.completion_tokens_details = None
    return response


_BAD_PASS_ARGS = '{"until":"invalid_value"}'
_PASS_ERROR = '{"error": "Invalid until value: invalid_value"}'
_PASS_OK = '{"action_pending": false, "stop_reason": "passed"}'
_TOOLS = [{"type": "function", "function": {"name": "pass_priority", "parameters": {}}}]


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_run_pilot_loop_raises_on_tool_failure():
    session = MagicMock()
    session.call_tool = AsyncMock(side_effect=RuntimeError("bridge died"))

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_make_llm_response("pass_priority", "{}"))

    with pytest.raises(ToolExecutionError, match="MCP tool pass_priority failed: bridge died"):
        await run_pilot_loop(
            session=session,
            client=client,
            model="test-model",
            system_prompt="You are a test.",
            tools=_TOOLS,
            prices={},
            username="test-player",
        )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_run_pilot_loop_logs_failed_tool_call_before_reraising():
    session = MagicMock()
    session.call_tool = AsyncMock(side_effect=RuntimeError("bridge died"))

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_make_llm_response("choose_action", '{"choice":"no"}'))

    game_log = MagicMock()
    with (
        patch("puppeteer.pilot.log_error") as log_error_mock,
        pytest.raises(ToolExecutionError, match="MCP tool choose_action failed: bridge died"),
    ):
        await run_pilot_loop(
            session=session,
            client=client,
            model="test-model",
            system_prompt="You are a test.",
            tools=[{"type": "function", "function": {"name": "choose_action", "parameters": {}}}],
            prices={},
            username="test-player",
            game_dir=Path("/tmp/test-game"),
            game_log=game_log,
        )

    failed_tool_calls = [call for call in game_log.emit.call_args_list if call.args and call.args[0] == "tool_call"]
    assert len(failed_tool_calls) == 1
    failed_call = failed_tool_calls[0]
    assert failed_call.kwargs["tool"] == "choose_action"
    assert failed_call.kwargs["arguments"] == {"choice": "no"}
    failed_result = json.loads(failed_call.kwargs["result"])
    assert failed_result == {
        "success": False,
        "error": "MCP tool choose_action failed: bridge died",
        "error_code": "tool_execution_error",
        "retryable": False,
    }
    game_log.emit.assert_any_call(
        "llm_error",
        error_type="ToolExecutionError",
        error_message="MCP tool choose_action failed: bridge died",
    )
    log_error_mock.assert_called_once()
    assert log_error_mock.call_args.args[3] == "[pilot] Fatal tool error: MCP tool choose_action failed: bridge died"


@pytest.mark.asyncio
async def test_repeated_pass_error_forces_plain_pass():
    """After 3 identical pass_priority errors, pilot forces a plain pass."""
    session = MagicMock()
    tool_calls = []
    pass_call_count = 0

    async def fake_call_tool(name, _args):
        nonlocal pass_call_count
        tool_calls.append((name, dict(_args) if _args else {}))
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
            prices={},
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
            if (not args or args == {}) and pass_call_count > 1:  # not prefetch
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
            prices={},
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
            if (not args or args == {}) and pass_call_count > 1:  # not prefetch
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
            prices={},
            username="test-player",
        )

    # No forced plain pass should have been made (never hit 3 consecutive)
    assert forced_pass_count == 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_repeated_truncation_resets_board_context():
    """Repeated truncation should clear board context before the next pass."""
    session = MagicMock()
    tool_calls: list[tuple[str, dict]] = []

    async def fake_call_tool(name: str, args: dict) -> MagicMock:
        tool_calls.append((name, dict(args)))
        if name != "pass_priority":
            return _mock_tool_result("{}")
        if len([call for call in tool_calls if call[0] == "pass_priority"]) == 1:
            return _mock_tool_result(
                json.dumps(
                    {
                        "action_pending": True,
                        "action_type": "GAME_SELECT",
                        "board": [{"name": "You", "life": 20}],
                        "board_cursor": 7,
                    }
                )
            )
        return _mock_tool_result('{"game_over": true}')

    session.call_tool = AsyncMock(side_effect=fake_call_tool)
    client = MagicMock()

    async def fake_create(**_kwargs):
        if fake_create.calls == 0:
            fake_create.calls += 1
            return _make_llm_response("pass_priority", "{}")
        if fake_create.calls == 1:
            fake_create.calls += 1
            return _make_truncated_response()
        fake_create.calls += 1
        return _make_llm_response("pass_priority", "{}")

    fake_create.calls = 0
    client.chat.completions.create = AsyncMock(side_effect=fake_create)
    game_log = MagicMock()

    with (
        patch("puppeteer.pilot.MAX_CONSECUTIVE_TRUNCATIONS", 1),
        patch("puppeteer.pilot.auto_pass_loop", new_callable=AsyncMock),
    ):
        await asyncio.wait_for(
            run_pilot_loop(
                session=session,
                client=client,
                model="test-model",
                system_prompt="You are a test.",
                tools=_TOOLS,
                prices={},
                username="test-player",
                game_log=game_log,
            ),
            timeout=2,
        )

    pass_calls = [args for name, args in tool_calls if name == "pass_priority"]
    assert pass_calls == [{}, {}]
    game_log.emit.assert_any_call("context_reset", reason="repeated_truncations")


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_repeated_timeout_resets_board_context():
    """Repeated timeout recovery should force a fresh board on the next pass."""
    session = MagicMock()
    tool_calls: list[tuple[str, dict]] = []
    pass_call_count = 0

    async def fake_call_tool(name: str, args: dict) -> MagicMock:
        nonlocal pass_call_count
        tool_calls.append((name, dict(args)))
        if name != "pass_priority":
            return _mock_tool_result("{}")
        pass_call_count += 1
        if pass_call_count == 1:
            return _mock_tool_result(
                json.dumps(
                    {
                        "action_pending": True,
                        "action_type": "GAME_SELECT",
                        "board": [{"name": "You", "life": 20}],
                        "board_cursor": 11,
                    }
                )
            )
        if pass_call_count == 2:
            return _mock_tool_result('{"action_pending": false}')
        return _mock_tool_result('{"game_over": true}')

    session.call_tool = AsyncMock(side_effect=fake_call_tool)
    client = MagicMock()

    async def fake_create(**_kwargs):
        if fake_create.calls == 0:
            fake_create.calls += 1
            return _make_llm_response("pass_priority", "{}")
        if fake_create.calls == 1:
            fake_create.calls += 1
            raise TimeoutError()
        fake_create.calls += 1
        return _make_llm_response("pass_priority", "{}")

    fake_create.calls = 0
    client.chat.completions.create = AsyncMock(side_effect=fake_create)
    game_log = MagicMock()

    with (
        patch("puppeteer.pilot.MAX_CONSECUTIVE_TIMEOUTS", 1),
        patch("puppeteer.pilot.auto_pass_loop", new_callable=AsyncMock),
    ):
        await asyncio.wait_for(
            run_pilot_loop(
                session=session,
                client=client,
                model="test-model",
                system_prompt="You are a test.",
                tools=_TOOLS,
                prices={},
                username="test-player",
                game_log=game_log,
            ),
            timeout=2,
        )

    pass_calls = [args for name, args in tool_calls if name == "pass_priority"]
    assert pass_calls == [{}, {}, {}]
    game_log.emit.assert_any_call("context_reset", reason="repeated_timeouts")


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_transient_error_reset_preserves_board_context():
    """Transient LLM errors should keep the last board cursor for the next pass."""
    session = MagicMock()
    tool_calls: list[tuple[str, dict]] = []
    pass_call_count = 0

    async def fake_call_tool(name: str, args: dict) -> MagicMock:
        nonlocal pass_call_count
        tool_calls.append((name, dict(args)))
        if name != "pass_priority":
            return _mock_tool_result("{}")
        pass_call_count += 1
        if pass_call_count == 1:
            return _mock_tool_result(
                json.dumps(
                    {
                        "action_pending": True,
                        "action_type": "GAME_SELECT",
                        "board": [{"name": "You", "life": 20}],
                        "board_cursor": 5,
                    }
                )
            )
        if pass_call_count == 2:
            return _mock_tool_result('{"action_pending": false}')
        return _mock_tool_result('{"game_over": true}')

    session.call_tool = AsyncMock(side_effect=fake_call_tool)
    client = MagicMock()

    async def fake_create(**_kwargs):
        if fake_create.calls == 0:
            fake_create.calls += 1
            return _make_llm_response("pass_priority", "{}")
        if fake_create.calls == 1:
            fake_create.calls += 1
            raise OpenAIError("temporary upstream failure")
        fake_create.calls += 1
        return _make_llm_response("pass_priority", "{}")

    fake_create.calls = 0
    client.chat.completions.create = AsyncMock(side_effect=fake_create)

    with patch("puppeteer.pilot.auto_pass_loop", new_callable=AsyncMock):
        await asyncio.wait_for(
            run_pilot_loop(
                session=session,
                client=client,
                model="test-model",
                system_prompt="You are a test.",
                tools=_TOOLS,
                prices={},
                username="test-player",
            ),
            timeout=2,
        )

    pass_calls = [args for name, args in tool_calls if name == "pass_priority"]
    assert pass_calls == [{}, {}, {"board_cursor": 5}]


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_stall_recovery_preserves_board_context():
    """Stall recovery should keep board cursor state for the next pilot turn."""
    session = MagicMock()
    tool_calls: list[tuple[str, dict]] = []
    pass_call_count = 0

    async def fake_call_tool(name: str, args: dict) -> MagicMock:
        nonlocal pass_call_count
        tool_calls.append((name, dict(args)))
        if name == "send_chat_message":
            return _mock_tool_result('{"success": true}')
        if name != "pass_priority":
            return _mock_tool_result("{}")
        pass_call_count += 1
        if pass_call_count == 1:
            return _mock_tool_result(
                json.dumps(
                    {
                        "action_pending": True,
                        "action_type": "GAME_SELECT",
                        "board": [{"name": "You", "life": 20}],
                        "board_cursor": 9,
                    }
                )
            )
        if pass_call_count == 2:
            return _mock_tool_result('{"action_pending": false}')
        return _mock_tool_result('{"game_over": true}')

    session.call_tool = AsyncMock(side_effect=fake_call_tool)
    client = MagicMock()

    async def fake_create(**_kwargs):
        if fake_create.calls == 0:
            fake_create.calls += 1
            return _make_llm_response("pass_priority", "{}")
        fake_create.calls += 1
        return _make_llm_response("pass_priority", "{}")

    fake_create.calls = 0
    client.chat.completions.create = AsyncMock(side_effect=fake_create)
    game_log = MagicMock()

    with (
        patch("puppeteer.pilot.MAX_TURNS_WITHOUT_PROGRESS", 1),
        patch("puppeteer.pilot.auto_pass_loop", new_callable=AsyncMock),
    ):
        await asyncio.wait_for(
            run_pilot_loop(
                session=session,
                client=client,
                model="test-model",
                system_prompt="You are a test.",
                tools=_TOOLS,
                prices={},
                username="test-player",
                game_log=game_log,
            ),
            timeout=2,
        )

    pass_calls = [args for name, args in tool_calls if name == "pass_priority"]
    assert pass_calls == [{}, {}, {"board_cursor": 9}]
    game_log.emit.assert_any_call("stall", turns_without_progress=1, last_tools=["pass_priority"])


# --- Chat rate limiting tests ---


def _make_multi_tool_response(tool_calls_spec: list[tuple[str, str]]) -> MagicMock:
    """Create a mock LLM response with multiple tool calls.

    *tool_calls_spec* is a list of (tool_name, arguments_json) tuples.
    """
    tool_calls = []
    for i, (name, arguments) in enumerate(tool_calls_spec):
        tc = MagicMock()
        tc.id = f"call_{i}"
        tc.function.name = name
        tc.function.arguments = arguments
        tool_calls.append(tc)

    choice = MagicMock()
    choice.finish_reason = "tool_calls"
    choice.message.tool_calls = tool_calls
    choice.message.content = None

    response = MagicMock()
    response.choices = [choice]
    response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    response.usage.prompt_tokens_details = None
    response.usage.completion_tokens_details = None
    return response


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_chat_messages_rate_limited_per_turn():
    """send_chat_message calls beyond MAX_CHAT_MESSAGES_PER_TURN should be blocked."""
    session = MagicMock()
    chat_calls_forwarded = 0

    async def fake_call_tool(name, _args):
        nonlocal chat_calls_forwarded
        if name == "send_chat_message":
            chat_calls_forwarded += 1
            return _mock_tool_result('{"success": true}')
        if name == "pass_priority":
            return _mock_tool_result('{"game_over": true}')
        return _mock_tool_result('{"ok": true}')

    session.call_tool = AsyncMock(side_effect=fake_call_tool)

    # LLM sends 4 chat messages + pass_priority in one turn
    chat_count = MAX_CHAT_MESSAGES_PER_TURN + 2
    tool_calls = [("send_chat_message", json.dumps({"message": f"msg {i}"})) for i in range(chat_count)]
    tool_calls.append(("pass_priority", '{"timeout_ms": 10000}'))
    llm_response = _make_multi_tool_response(tool_calls)

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=llm_response)

    tools = [
        {"type": "function", "function": {"name": "send_chat_message", "parameters": {}}},
        {"type": "function", "function": {"name": "pass_priority", "parameters": {}}},
    ]

    with patch("puppeteer.pilot.auto_pass_loop", new_callable=AsyncMock):
        await run_pilot_loop(
            session=session,
            client=client,
            model="test-model",
            system_prompt="You are a test.",
            tools=tools,
            prices={},
            username="test-player",
        )

    # Only MAX_CHAT_MESSAGES_PER_TURN should have been forwarded to the bridge
    assert chat_calls_forwarded == MAX_CHAT_MESSAGES_PER_TURN


# --- Pilot rendering tests ---


def _sample_pass_priority_result() -> dict:
    """A realistic pass_priority result with board and choices."""
    return {
        "game_seq": 42,
        "action_type": "GAME_SELECT",
        "context": "T3 Precombat Main/Precombat Main (Alice) YOUR_MAIN",
        "stop_reason": "playable_cards",
        "response_type": "select",
        "respond_with": "choice=pN to play, or choice=no to pass",
        "message": "Play spells and abilities",
        "land_drops_used": 0,
        "action_pending": True,
        "board": [
            {
                "name": "Alice",
                "is_you": True,
                "is_active": True,
                "life": 20,
                "library_size": 50,
                "hand_size": 2,
                "hand": [
                    {
                        "name": "Lightning Bolt",
                        "mana_cost": "{R}",
                        "id": "p3",
                        "rules": ["Lightning Bolt deals 3 damage to any target."],
                    },
                    {"name": "Mountain", "is_land": True, "id": "p5", "rules": ["{T}: Add {R}."]},
                ],
                "battlefield": [{"name": "Mountain", "is_land": True, "id": "p1", "tapped": False}],
            },
            {
                "name": "Bob",
                "is_you": False,
                "is_active": False,
                "life": 18,
                "library_size": 52,
                "hand_size": 2,
                "battlefield": [{"name": "Island", "is_land": True, "id": "p10"}],
            },
        ],
        "choices": [
            {"name": "Lightning Bolt", "index": 0, "action": "cast", "mana_cost": "{R}", "id": "p3"},
            {"name": "Mountain", "index": 1, "action": "land", "id": "p5"},
        ],
    }


class TestRenderForPilot:
    def test_basic_render(self) -> None:
        result = json.dumps(_sample_pass_priority_result())
        text, board = render_for_pilot(result, None)
        assert "Alice" in text
        assert "Lightning Bolt" in text
        assert "Mountain" in text
        assert "id=p3" in text
        assert board is not None

    def test_non_action_passthrough(self) -> None:
        result = json.dumps({"stop_reason": "game_over", "game_over": True})
        text, board = render_for_pilot(result, None)
        # Non-action_pending results pass through as-is
        assert "game_over" in text
        assert board is None

    def test_board_unchanged_uses_last_board(self) -> None:
        # First call: has board
        first_result = json.dumps(_sample_pass_priority_result())
        text1, board = render_for_pilot(first_result, None)
        assert board is not None
        assert "Alice" in text1

        # Second call: no board (board_unchanged)
        no_board = _sample_pass_priority_result()
        del no_board["board"]
        second_result = json.dumps(no_board)
        text2, board2 = render_for_pilot(second_result, board)
        assert "Alice" in text2  # Still renders board from last_board
        assert board2 is board  # Board reference preserved

    def test_respond_with_line(self) -> None:
        result = json.dumps(_sample_pass_priority_result())
        text, _ = render_for_pilot(result, None)
        assert "Respond:" in text

    def test_card_reference_included(self) -> None:
        result = json.dumps(_sample_pass_priority_result())
        text, _ = render_for_pilot(result, None)
        assert "## Card Reference" in text
        assert "3 damage" in text

    def test_invalid_json_passthrough(self) -> None:
        text, board = render_for_pilot("not json", None)
        assert text == "not json"
        assert board is None

    def test_seen_oracle_cards_filters_repeat(self) -> None:
        result = json.dumps(_sample_pass_priority_result())
        seen: set[str] = set()
        text1, board = render_for_pilot(result, None, seen)
        assert "3 damage" in text1
        assert "Lightning Bolt" in seen
        # Second render: oracle text should not repeat
        text2, _ = render_for_pilot(result, board, seen)
        assert "3 damage" not in text2

    def test_seen_oracle_cards_none_always_shows(self) -> None:
        result = json.dumps(_sample_pass_priority_result())
        text1, board = render_for_pilot(result, None, None)
        assert "3 damage" in text1
        text2, _ = render_for_pilot(result, board, None)
        assert "3 damage" in text2


class TestExtractOracleTexts:
    def test_extracts_rules(self) -> None:
        board = _sample_pass_priority_result()["board"]
        texts = extract_oracle_texts_from_board(board)
        assert "Lightning Bolt" in texts
        assert "3 damage" in texts["Lightning Bolt"]["oracle_text"]

    def test_skips_basic_lands(self) -> None:
        board = _sample_pass_priority_result()["board"]
        texts = extract_oracle_texts_from_board(board)
        assert "Mountain" not in texts
        assert "Island" not in texts


class TestBuildPilotDecision:
    def test_parses_context(self) -> None:
        data = _sample_pass_priority_result()
        decision = build_pilot_decision(data)
        assert isinstance(decision, Decision)
        assert decision.turn == 3
        assert "PRECOMBAT" in decision.phase
        assert decision.player == "Alice"

    def test_choices_preserved(self) -> None:
        data = _sample_pass_priority_result()
        decision = build_pilot_decision(data)
        assert isinstance(decision, Decision)
        assert len(decision.choices) == 2

    def test_pilot_context(self) -> None:
        data = _sample_pass_priority_result()
        decision = build_pilot_decision(data)
        assert isinstance(decision, Decision)
        assert isinstance(decision.pilot_context, PilotContext)
        assert decision.pilot_context.land_drops_used == 0

    def test_preserves_empty_pregame_phase_marker(self) -> None:
        data = _sample_pass_priority_result()
        data["context"] = "T1 ()"
        decision = build_pilot_decision(data)
        assert isinstance(decision, Decision)
        assert decision.turn == 1
        assert decision.phase == "()"


class TestBuildPilotSnapshot:
    def test_player_data(self) -> None:
        data = _sample_pass_priority_result()
        decision = build_pilot_decision(data)
        snapshot = build_pilot_snapshot(data, data["board"], decision)
        assert len(snapshot.players) == 2
        assert snapshot.players[0].name == "Alice"
        assert snapshot.players[0].life == 20
        assert len(snapshot.players[0].hand) == 2
        assert snapshot.players[0].hand_count == 2

    def test_no_board(self) -> None:
        data = _sample_pass_priority_result()
        decision = build_pilot_decision(data)
        snapshot = build_pilot_snapshot(data, None, decision)
        assert snapshot.players == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_prefetch")
async def test_consecutive_empty_choices_triggers_auto_pass():
    """After MAX_CONSECUTIVE_EMPTY_CHOICES empty responses, pilot should switch to auto-pass."""
    session = _make_session()

    # Mock LLM to always return empty choices
    response = MagicMock()
    response.choices = []
    response.usage = MagicMock(prompt_tokens=10, completion_tokens=0)

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)

    with patch("puppeteer.pilot.auto_pass_loop", new_callable=AsyncMock) as mock_auto_pass:
        await run_pilot_loop(
            session=session,
            client=client,
            model="test-model",
            system_prompt="You are a test.",
            tools=[],
            prices={},
            username="test-player",
        )
        mock_auto_pass.assert_called_once()

    assert client.chat.completions.create.call_count == MAX_CONSECUTIVE_EMPTY_CHOICES
