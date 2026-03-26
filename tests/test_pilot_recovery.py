"""Tests for pilot recovery helpers (timeout and stall game-over detection)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import CallToolResult, TextContent

from magebench.pilot.pilot_recovery import _handle_timeout, _recover_from_stall
from magebench.pilot.pilot_state import PilotLoopState

logger = MagicMock()


def _mock_tool_result(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)])


def _make_session(pass_priority_text: str) -> MagicMock:
    session = MagicMock()
    session.call_tool = AsyncMock(
        return_value=_mock_tool_result(pass_priority_text),
    )
    return session


# ---------------------------------------------------------------------------
# _handle_timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_timeout_detects_game_over():
    session = _make_session(json.dumps({"game_over": True}))
    state = PilotLoopState(history=[])
    game_log = MagicMock()

    result = await _handle_timeout(
        session,
        state,
        game_log,
        logger=logger,
        llm_request_timeout_secs=30,
        max_consecutive_timeouts=3,
    )

    assert result is True
    game_log.emit.assert_any_call("auto_pilot_mode", reason="game_over")


@pytest.mark.asyncio
async def test_handle_timeout_detects_player_dead():
    session = _make_session(json.dumps({"player_dead": True}))
    state = PilotLoopState(history=[])
    game_log = MagicMock()

    result = await _handle_timeout(
        session,
        state,
        game_log,
        logger=logger,
        llm_request_timeout_secs=30,
        max_consecutive_timeouts=3,
    )

    assert result is True
    game_log.emit.assert_any_call("auto_pilot_mode", reason="player_dead")


@pytest.mark.asyncio
async def test_handle_timeout_returns_false_on_normal_result():
    session = _make_session(json.dumps({"action_pending": False}))
    state = PilotLoopState(history=[])

    result = await _handle_timeout(
        session,
        state,
        None,
        logger=logger,
        llm_request_timeout_secs=30,
        max_consecutive_timeouts=3,
    )

    assert result is False


@pytest.mark.asyncio
async def test_handle_timeout_detects_stop_reason_game_over():
    session = _make_session(json.dumps({"stop_reason": "game_over", "action_pending": False}))
    state = PilotLoopState(history=[])
    game_log = MagicMock()

    result = await _handle_timeout(
        session,
        state,
        game_log,
        logger=logger,
        llm_request_timeout_secs=30,
        max_consecutive_timeouts=3,
    )

    assert result is True
    game_log.emit.assert_any_call("auto_pilot_mode", reason="game_over")


@pytest.mark.asyncio
async def test_handle_timeout_returns_false_on_tool_error():
    session = MagicMock()
    session.call_tool = AsyncMock(side_effect=RuntimeError("bridge died"))
    state = PilotLoopState(history=[])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(asyncio, "sleep", AsyncMock())
        result = await _handle_timeout(
            session,
            state,
            None,
            logger=logger,
            llm_request_timeout_secs=30,
            max_consecutive_timeouts=3,
        )

    assert result is False


# ---------------------------------------------------------------------------
# _recover_from_stall
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recover_from_stall_detects_game_over():
    call_count = 0

    async def fake_call_tool(name, _args):
        nonlocal call_count
        call_count += 1
        if name == "send_chat_message":
            return _mock_tool_result('{"success": true}')
        return _mock_tool_result(json.dumps({"game_over": True}))

    session = MagicMock()
    session.call_tool = AsyncMock(side_effect=fake_call_tool)
    state = PilotLoopState(history=[])
    state.turns_without_progress = 5
    game_log = MagicMock()

    result = await _recover_from_stall(session, state, game_log, {"pass_priority"}, logger=logger)

    assert result is True
    game_log.emit.assert_any_call("auto_pilot_mode", reason="game_over")
    # Should not reset context when game ended
    assert state.history == []


@pytest.mark.asyncio
async def test_recover_from_stall_detects_player_dead():
    async def fake_call_tool(name, _args):
        if name == "send_chat_message":
            return _mock_tool_result('{"success": true}')
        return _mock_tool_result(json.dumps({"player_dead": True}))

    session = MagicMock()
    session.call_tool = AsyncMock(side_effect=fake_call_tool)
    state = PilotLoopState(history=[])
    state.turns_without_progress = 5
    game_log = MagicMock()

    result = await _recover_from_stall(session, state, game_log, {"pass_priority"}, logger=logger)

    assert result is True
    game_log.emit.assert_any_call("auto_pilot_mode", reason="player_dead")


@pytest.mark.asyncio
async def test_recover_from_stall_returns_false_on_normal_result():
    async def fake_call_tool(name, _args):
        if name == "send_chat_message":
            return _mock_tool_result('{"success": true}')
        return _mock_tool_result(json.dumps({"action_pending": False}))

    session = MagicMock()
    session.call_tool = AsyncMock(side_effect=fake_call_tool)
    state = PilotLoopState(history=[])
    state.turns_without_progress = 5

    result = await _recover_from_stall(session, state, None, {"pass_priority"}, logger=logger)

    assert result is False
    # Should reset context when game continues
    assert len(state.history) == 1
    assert state.history[0]["content"] == "A new turn has started. Call pass_priority to continue."
