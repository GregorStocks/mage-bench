"""Shared auto-pass loop for LLM fallback mode.

When an LLM becomes non-functional (degraded, credits exhausted, model not
found), the puppeteer falls back to repeatedly calling pass_priority until
the game ends. This module provides that shared loop so pilot.py doesn't
duplicate it.
"""

import asyncio
import json

from mcp import ClientSession, McpError

from puppeteer.log import get_logger
from puppeteer.tool_error import ToolExecutionError, extract_text_content

logger = get_logger(__name__)

MAX_AUTO_PASS_ITERATIONS = 500  # ~80+ min at 10s/iteration
MAX_CONSECUTIVE_ERRORS = 20  # 20 * 5s = ~100s of continuous failure


async def _execute_tool(session: ClientSession, name: str, arguments: dict) -> str:
    try:
        result = await session.call_tool(name, arguments)
    except (McpError, OSError, RuntimeError) as exc:
        raise ToolExecutionError(f"MCP tool {name} failed: {exc}") from exc
    return extract_text_content(name, result)


async def auto_pass_loop(
    session: ClientSession,
    label: str,
    max_iterations: int = MAX_AUTO_PASS_ITERATIONS,
    max_consecutive_errors: int = MAX_CONSECUTIVE_ERRORS,
) -> None:
    """Run pass_priority in a loop until game over or error threshold.

    Used when the LLM is no longer functional and the game must finish on
    autopilot.
    """
    consecutive_errors = 0
    for _ in range(max_iterations):
        try:
            result_text = await _execute_tool(session, "pass_priority", {})
            try:
                result_data = json.loads(result_text)
            except (json.JSONDecodeError, TypeError):
                result_data = {}
            if result_data.get("game_over") or result_data.get("player_dead"):
                logger.info("[%s] Game over detected, exiting auto-pass loop", label)
                return
            if "error" in result_data:
                consecutive_errors += 1
                logger.warning("[%s] Auto-pass error: %s", label, result_data["error"])
                if consecutive_errors >= max_consecutive_errors:
                    logger.warning("[%s] Too many consecutive errors, exiting", label)
                    return
                await asyncio.sleep(5)
            else:
                consecutive_errors = 0
        except ToolExecutionError as pass_err:
            consecutive_errors += 1
            logger.warning("[%s] Auto-pass exception: %s", label, pass_err)
            if consecutive_errors >= max_consecutive_errors:
                logger.warning("[%s] Too many consecutive errors, exiting", label)
                return
            await asyncio.sleep(5)
    logger.warning("[%s] Auto-pass loop reached max iterations, exiting", label)
