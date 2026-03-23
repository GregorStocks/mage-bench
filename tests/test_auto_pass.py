"""Tests for the shared auto_pass_loop utility."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import CallToolResult, TextContent

from magebench.pilot.auto_pass import auto_pass_loop


@pytest.fixture(autouse=True)
def _no_sleep():
    """Patch asyncio.sleep to avoid real delays in auto_pass_loop tests."""
    with patch("magebench.pilot.auto_pass.asyncio.sleep", new_callable=AsyncMock):
        yield


def _make_session(responses: list[str]) -> MagicMock:
    """Create a mock MCP session that returns the given responses in order."""
    session = MagicMock()
    results = [CallToolResult(content=[TextContent(type="text", text=text)]) for text in responses]
    session.call_tool = AsyncMock(side_effect=results)
    return session


@pytest.mark.asyncio
async def test_game_over_exits_immediately():
    session = _make_session([json.dumps({"game_over": True})])
    await auto_pass_loop(session, "test")
    assert session.call_tool.call_count == 1


@pytest.mark.asyncio
async def test_player_dead_exits_immediately():
    """Dead player exits auto-pass loop immediately to avoid log spam."""
    session = _make_session([json.dumps({"player_dead": True})])
    await auto_pass_loop(session, "test")
    assert session.call_tool.call_count == 1


@pytest.mark.asyncio
async def test_consecutive_errors_cause_exit():
    max_errors = 3
    responses = [json.dumps({"error": "something broke"})] * (max_errors + 1)
    session = _make_session(responses)
    await auto_pass_loop(session, "test", max_consecutive_errors=max_errors)
    assert session.call_tool.call_count == max_errors


@pytest.mark.asyncio
async def test_successful_calls_reset_error_counter():
    max_errors = 3
    # 2 errors, then a success, then 2 more errors, then game_over
    responses = [
        json.dumps({"error": "fail"}),
        json.dumps({"error": "fail"}),
        json.dumps({}),  # success resets counter
        json.dumps({"error": "fail"}),
        json.dumps({"error": "fail"}),
        json.dumps({"game_over": True}),
    ]
    session = _make_session(responses)
    await auto_pass_loop(session, "test", max_consecutive_errors=max_errors)
    # Should have processed all 6 responses (errors never hit threshold)
    assert session.call_tool.call_count == 6


@pytest.mark.asyncio
async def test_max_iterations_causes_exit():
    max_iter = 5
    responses = [json.dumps({})] * max_iter
    session = _make_session(responses)
    await auto_pass_loop(session, "test", max_iterations=max_iter)
    assert session.call_tool.call_count == max_iter


@pytest.mark.asyncio
async def test_exception_counts_as_error():
    max_errors = 2
    session = MagicMock()
    session.call_tool = AsyncMock(side_effect=RuntimeError("connection lost"))
    await auto_pass_loop(session, "test", max_consecutive_errors=max_errors)
    assert session.call_tool.call_count == max_errors


@pytest.mark.asyncio
async def test_logs_errors_as_warnings(tmp_path: Path, caplog):
    """Auto-pass errors are logged as warnings, not written to the error log file.

    These are LLM degradation issues, not code bugs, so they shouldn't surface
    as critical errors on the website.
    """
    max_errors = 2
    responses = [json.dumps({"error": "broken"})] * (max_errors + 1)
    session = _make_session(responses)
    await auto_pass_loop(session, "test", max_consecutive_errors=max_errors)
    # Should NOT write to the error log file
    error_log = tmp_path / "player1_errors.log"
    assert not error_log.exists()
    # Should appear in log output as warnings
    assert "Auto-pass error: broken" in caplog.text
    assert "Too many consecutive errors" in caplog.text
