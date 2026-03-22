"""Recovery and error-handling helpers for the pilot loop."""

import asyncio
from logging import Logger
from typing import Protocol

from mcp import ClientSession

from magebench.pilot.pilot_bridge import execute_tool
from magebench.pilot.pilot_state import PilotLoopState, reset_context
from magebench.pilot.tool_error import ToolExecutionError
from puppeteer.game_log import GameLogWriter


class _ChoiceLike(Protocol):
    finish_reason: str | None


class _UsageLike(Protocol):
    completion_tokens: int | None


class _ResponseLike(Protocol):
    usage: _UsageLike | None


def _handle_truncated_response(
    state: PilotLoopState,
    choice: _ChoiceLike,
    response: _ResponseLike,
    game_log: GameLogWriter | None,
    *,
    logger: Logger,
    max_tokens: int,
    max_consecutive_truncations: int,
) -> bool:
    """Handle max-token truncation and reset context after repeated failures."""
    if choice.finish_reason != "length":
        state.consecutive_truncations = 0
        return False

    state.consecutive_truncations += 1
    tokens_used = (response.usage.completion_tokens or 0) if response.usage else "?"
    logger.warning(
        "[pilot] OUTPUT TRUNCATED: finish_reason=length, completion_tokens=%s/%s. "
        "Model hit max_tokens cap before producing a tool call. [%d]",
        tokens_used,
        max_tokens,
        state.consecutive_truncations,
    )
    if state.consecutive_truncations < max_consecutive_truncations:
        return False

    logger.warning("[pilot] Repeated truncations, resetting conversation context")
    if game_log:
        game_log.emit("context_reset", reason="repeated_truncations")
    reset_context(
        state,
        "Continue playing. Be concise. Call pass_priority.",
        reset_board_context=True,
    )
    state.consecutive_truncations = 0
    return True


async def _recover_from_stall(
    session: ClientSession,
    state: PilotLoopState,
    game_log: GameLogWriter | None,
    turn_tools_called: set[str],
    *,
    logger: Logger,
) -> None:
    """Auto-pass once, then reset conversation after a stalled turn sequence."""
    last_tools = sorted(turn_tools_called)
    logger.warning(
        "[pilot] Stalled: %d turns without progress, last tools: %s, auto-passing until next event",
        state.turns_without_progress,
        last_tools or "none",
    )
    if game_log:
        game_log.emit(
            "stall",
            turns_without_progress=state.turns_without_progress,
            last_tools=last_tools,
        )
    try:
        await execute_tool(
            session,
            "send_chat_message",
            {"message": "Brain freeze! Auto-passing until next turn..."},
        )
    except ToolExecutionError:
        pass
    try:
        await execute_tool(session, "pass_priority", {})
        logger.info("[pilot] Auto-passed stalled action")
    except ToolExecutionError as exc:
        logger.warning("[pilot] Auto-pass failed: %s", exc)

    state.turns_without_progress = 0
    reset_context(
        state,
        "A new turn has started. Call pass_priority to continue.",
        reset_board_context=False,
    )


async def _handle_timeout(
    session: ClientSession,
    state: PilotLoopState,
    game_log: GameLogWriter | None,
    *,
    logger: Logger,
    llm_request_timeout_secs: int,
    max_consecutive_timeouts: int,
) -> None:
    """Keep the game moving across request timeouts and reset repeated failures."""
    state.consecutive_timeouts += 1
    logger.warning(
        "[pilot] LLM request timed out after %ss [%d]",
        llm_request_timeout_secs,
        state.consecutive_timeouts,
    )
    if game_log:
        game_log.emit(
            "llm_error",
            error_type="timeout",
            error_message=f"Timed out after {llm_request_timeout_secs}s [{state.consecutive_timeouts}]",
        )
    try:
        await execute_tool(session, "pass_priority", {})
    except ToolExecutionError:
        await asyncio.sleep(5)

    if state.consecutive_timeouts < max_consecutive_timeouts:
        return

    logger.warning("[pilot] Repeated LLM timeouts, resetting conversation context")
    if game_log:
        game_log.emit("context_reset", reason="repeated_timeouts")
    reset_context(
        state,
        "Continue playing. Call pass_priority.",
        reset_board_context=True,
    )
    state.consecutive_timeouts = 0


def _classify_permanent_llm_failure(error_str: str) -> str | None:
    """Return the permanent failure reason, if the error should abort the game."""
    permanent_codes = {"401", "402", "403", "404"}
    if not any(code in error_str for code in permanent_codes):
        return None
    is_not_found = "404" in error_str and "401" not in error_str
    return "Model not found" if is_not_found else "Credits exhausted"
