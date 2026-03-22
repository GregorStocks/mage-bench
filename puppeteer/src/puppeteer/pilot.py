"""Pilot: LLM-powered game player that makes strategic decisions via MCP tools."""

import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Protocol

from mcp import ClientSession
from openai import AsyncOpenAI, OpenAIError

from puppeteer.auto_pass import auto_pass_loop
from puppeteer.bridge_transport import build_bridge_launch_args, spawn_bridge_http
from puppeteer.config import load_prompts
from puppeteer.game_log import GameLogWriter
from puppeteer.llm_cost import (
    DEFAULT_LLM_PROVIDER,
    SUPPORTED_LLM_PROVIDERS,
    get_model_price,
    llm_base_url,
    load_prices,
    required_api_key_env,
    write_cost_file,
)
from puppeteer.log import get_logger, log_error, setup_logging
from puppeteer.pilot_bridge import (
    _record_tool_execution_failure as _record_tool_execution_failure_impl,
)
from puppeteer.pilot_bridge import _tool_execution_error_result, execute_tool, mcp_tools_to_openai
from puppeteer.pilot_recovery import (
    _classify_permanent_llm_failure,
)
from puppeteer.pilot_recovery import (
    _handle_timeout as _handle_timeout_impl,
)
from puppeteer.pilot_recovery import (
    _handle_truncated_response as _handle_truncated_response_impl,
)
from puppeteer.pilot_recovery import (
    _recover_from_stall as _recover_from_stall_impl,
)
from puppeteer.pilot_rendering import (
    CONTEXT_RECENT_COUNT,
    RENDER_INTERVAL,
    _fetch_state_summary,
    _find_cache_breakpoint_idx,
    _with_cache_control,
    render_context,
    render_for_pilot,
)
from puppeteer.pilot_state import PilotLoopState, PilotTurnState, reset_context
from puppeteer.tool_error import ToolExecutionError

logger = get_logger(__name__)

DEFAULT_MODEL = "google/gemini-2.0-flash-001"

# Exit code returned when the LLM permanently fails (404 model not found,
# 402/403 credits exhausted). The orchestrator checks for this to abort the
# game early instead of wasting API tokens on the other player.
PERMANENT_FAILURE_EXIT_CODE = 3

MAX_TOKENS = 20_000
LLM_REQUEST_TIMEOUT_SECS = 120
MAX_CONSECUTIVE_TIMEOUTS = 3
MAX_CONSECUTIVE_EMPTY_CHOICES = 5
MAX_GAME_DURATION_SECS = 3 * 3600  # 3 hours absolute maximum
MAX_TURNS_WITHOUT_PROGRESS = 20
MAX_CONSECUTIVE_PASS_ERRORS = 3
MAX_CONSECUTIVE_TRUNCATIONS = 3
MAX_CONSECUTIVE_EMPTY_ERRORS = 10  # bridge is dead if every tool returns empty error
MAX_EMPTY_RESPONSES = 10
MAX_CHAT_MESSAGES_PER_TURN = 2  # max send_chat_message calls per LLM iteration

__all__ = [
    "DEFAULT_MODEL",
    "MAX_CHAT_MESSAGES_PER_TURN",
    "MAX_CONSECUTIVE_EMPTY_CHOICES",
    "MAX_TOKENS",
    "PERMANENT_FAILURE_EXIT_CODE",
    "PermanentLLMError",
    "build_initial_message",
    "main",
    "run_pilot",
    "run_pilot_loop",
]


class PermanentLLMError(Exception):
    """Raised when the LLM is permanently unreachable (model not found, credits exhausted)."""


class _ToolFunctionLike(Protocol):
    name: str
    arguments: str


class _ToolCallLike(Protocol):
    id: str
    function: _ToolFunctionLike


class _AssistantMessageLike(Protocol):
    content: str | None
    tool_calls: list[_ToolCallLike] | None


class _ChoiceLike(Protocol):
    finish_reason: str | None
    message: _AssistantMessageLike


class _UsageLike(Protocol):
    completion_tokens: int | None


class _ResponseLike(Protocol):
    usage: _UsageLike | None


def _record_tool_execution_failure(
    error: ToolExecutionError,
    username: str,
    game_dir: Path | None,
    game_log: GameLogWriter | None,
) -> None:
    """Persist fatal MCP tool failures so exports don't look falsely clean."""
    _record_tool_execution_failure_impl(
        error,
        username,
        game_dir,
        game_log,
        logger=logger,
        log_error_fn=log_error,
    )


def _handle_truncated_response(
    state: PilotLoopState,
    choice: _ChoiceLike,
    response: _ResponseLike,
    game_log: GameLogWriter | None,
) -> bool:
    """Handle max-token truncation and reset context after repeated failures."""
    return _handle_truncated_response_impl(
        state,
        choice,
        response,
        game_log,
        logger=logger,
        max_tokens=MAX_TOKENS,
        max_consecutive_truncations=MAX_CONSECUTIVE_TRUNCATIONS,
    )


async def _recover_from_stall(
    session: ClientSession,
    state: PilotLoopState,
    game_log: GameLogWriter | None,
    turn_tools_called: set[str],
) -> None:
    """Auto-pass once, then reset conversation after a stalled turn sequence."""
    await _recover_from_stall_impl(
        session,
        state,
        game_log,
        turn_tools_called,
        logger=logger,
    )


async def _handle_timeout(
    session: ClientSession,
    state: PilotLoopState,
    game_log: GameLogWriter | None,
) -> None:
    """Keep the game moving across request timeouts and reset repeated failures."""
    await _handle_timeout_impl(
        session,
        state,
        game_log,
        logger=logger,
        llm_request_timeout_secs=LLM_REQUEST_TIMEOUT_SECS,
        max_consecutive_timeouts=MAX_CONSECUTIVE_TIMEOUTS,
    )


# Tools that are purely informational (don't advance game state).
# Used by stall detection to classify LLM turns.
INFO_ONLY_TOOLS = {"get_game_state", "get_oracle_text", "send_chat_message"}


def _load_default_system_prompt() -> str:
    """Load the default system prompt from prompts.json."""
    prompts = load_prompts(None)
    assert "default" in prompts, "prompts.json must contain a 'default' key"
    return prompts["default"]


async def _build_loop_messages(
    state: PilotLoopState,
    session: ClientSession,
    system_prompt: str,
    cache_control: dict | None,
) -> list[dict]:
    """Render the next LLM request from the current history."""
    if len(state.history) > CONTEXT_RECENT_COUNT:
        state.render_counter += 1
        if not state.state_summary or state.render_counter % RENDER_INTERVAL == 0:
            state.state_summary = await _fetch_state_summary(session)
            state.render_counter = 0
        messages = render_context(state.history, system_prompt, state.state_summary, cache_control)
        state.cache_breakpoint_idx = _find_cache_breakpoint_idx(messages)
        return messages

    messages = render_context(state.history, system_prompt, state.state_summary, cache_control)
    state.cache_breakpoint_idx = len(messages) - 1 if messages else None
    state.render_counter = 0
    return messages


def _mark_tail_cache_breakpoint(
    messages: list[dict],
    state: PilotLoopState,
    cache_control: dict | None,
) -> None:
    """Mark the end of the stable prompt prefix for providers that cache it."""
    if not cache_control or len(messages) <= 1:
        return

    tail_idx = state.cache_breakpoint_idx if state.cache_breakpoint_idx is not None else len(messages) - 1
    marked = _with_cache_control(messages[tail_idx], cache_control)
    if marked is not messages[tail_idx]:
        messages[tail_idx] = marked


def _build_assistant_tool_message(message: _AssistantMessageLike) -> dict:
    """Build a provider-safe assistant message from an SDK tool response."""
    assistant_msg: dict = {"role": "assistant", "content": message.content}
    if message.tool_calls:
        assistant_msg["tool_calls"] = [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments,
                },
            }
            for tool_call in message.tool_calls
        ]
    return assistant_msg


def _maybe_extract_result_dict(result_text: str) -> dict | None:
    """Parse a JSON tool result when it is a dict."""
    try:
        data = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


async def _process_tool_calls(
    session: ClientSession,
    choice: _ChoiceLike,
    state: PilotLoopState,
    username: str,
    game_dir: Path | None,
    game_log: GameLogWriter | None,
) -> tuple[bool, set[str]]:
    """Execute a single LLM tool-calling turn."""
    turn_state = PilotTurnState()

    if choice.message.content:
        logger.info("[pilot] Thinking: %s", choice.message.content)
    state.empty_responses = 0
    state.last_was_empty = False
    state.history.append(_build_assistant_tool_message(choice.message))

    assert choice.message.tool_calls is not None, "expected tool_calls in LLM response"
    for tool_call in choice.message.tool_calls:
        fn = tool_call.function
        args = json.loads(fn.arguments) if fn.arguments else {}

        state.board_tracker.inject(fn.name, args)
        logger.info("[pilot] Tool: %s(%s)", fn.name, json.dumps(args, separators=(",", ":")))

        if fn.name == "send_chat_message" and turn_state.chat_messages_this_turn >= MAX_CHAT_MESSAGES_PER_TURN:
            result_text = json.dumps({"success": False, "error": "Chat limit reached — focus on gameplay."})
            tool_latency_ms = 0
        else:
            if fn.name == "send_chat_message":
                turn_state.chat_messages_this_turn += 1
            tool_start = time.monotonic()
            try:
                result_text = await execute_tool(session, fn.name, args)
            except ToolExecutionError as exc:
                tool_latency_ms = int((time.monotonic() - tool_start) * 1000)
                if game_log:
                    game_log.emit(
                        "tool_call",
                        call_id=tool_call.id,
                        tool=fn.name,
                        arguments=args,
                        result=_tool_execution_error_result(exc, state.last_game_seq),
                        latency_ms=tool_latency_ms,
                        game_seq=state.last_game_seq,
                    )
                raise
            tool_latency_ms = int((time.monotonic() - tool_start) * 1000)

        result_data = _maybe_extract_result_dict(result_text)
        if result_data and "game_seq" in result_data:
            state.last_game_seq = result_data["game_seq"]
        state.board_tracker.extract(result_text)

        if game_log:
            game_log.emit(
                "tool_call",
                call_id=tool_call.id,
                tool=fn.name,
                arguments=args,
                result=result_text,
                latency_ms=tool_latency_ms,
                game_seq=state.last_game_seq,
            )

        if result_text == '{"error": ""}':
            state.consecutive_empty_errors += 1
            if state.consecutive_empty_errors >= MAX_CONSECUTIVE_EMPTY_ERRORS:
                log_error(
                    logger,
                    game_dir,
                    username,
                    f"[pilot] {state.consecutive_empty_errors} consecutive empty errors — bridge is dead, exiting",
                )
                if game_log:
                    game_log.emit(
                        "auto_pilot_mode",
                        reason="bridge_dead",
                        consecutive_empty_errors=state.consecutive_empty_errors,
                    )
                return True, turn_state.tools_called
        else:
            state.consecutive_empty_errors = 0

        turn_state.tools_called.add(fn.name)
        if fn.name == "choose_action":
            choice_result = json.loads(result_text)
            action_taken = choice_result.get("action_taken")
            success = choice_result.get("success", False)
            if success:
                logger.info("[pilot] Action: %s", action_taken)
                turn_state.had_successful_action = True
                state.turns_without_progress = 0
            else:
                logger.warning("[pilot] Action failed: %s", choice_result.get("error"))
                turn_state.had_actionable_opportunity = True
        elif fn.name == "get_action_choices":
            choice_result = json.loads(result_text)
            action_type = choice_result.get("action_type")
            message = choice_result.get("message")
            choices = choice_result.get("choices")
            if choice_result.get("error"):
                turn_state.had_actionable_opportunity = True
            elif choices:
                logger.info("[pilot] Choices for %s: %d options", action_type, len(choices))
                turn_state.had_actionable_opportunity = True
            else:
                logger.info("[pilot] Action: %s - %s", action_type, message[:100] if message else "")
        elif fn.name == "pass_priority":
            try:
                pass_result = json.loads(result_text)
                context = pass_result.get("context")
                if context and context.startswith("T"):
                    try:
                        state.current_game_turn = int(context[1:].split()[0])
                    except (ValueError, IndexError):
                        pass
                if pass_result.get("action_pending"):
                    turn_state.had_actionable_opportunity = True
                    state.consecutive_pass_errors = 0
                    state.last_pass_error_msg = ""
                if pass_result.get("error"):
                    turn_state.had_actionable_opportunity = True
                    err_msg = pass_result["error"]
                    if err_msg == state.last_pass_error_msg:
                        state.consecutive_pass_errors += 1
                    else:
                        state.consecutive_pass_errors = 1
                        state.last_pass_error_msg = err_msg
                    if state.consecutive_pass_errors >= MAX_CONSECUTIVE_PASS_ERRORS:
                        logger.warning(
                            "[pilot] %d consecutive identical pass_priority errors, forcing plain pass",
                            state.consecutive_pass_errors,
                        )
                        if game_log:
                            game_log.emit(
                                "forced_pass",
                                reason="repeated_pass_error",
                                error=err_msg,
                                count=state.consecutive_pass_errors,
                            )
                        result_text = await execute_tool(session, "pass_priority", {})
                        state.consecutive_pass_errors = 0
                        state.last_pass_error_msg = ""
                else:
                    state.consecutive_pass_errors = 0
                    state.last_pass_error_msg = ""
            except (json.JSONDecodeError, TypeError):
                pass

        if fn.name == "send_chat_message":
            state.last_chat_turn = state.current_game_turn

        result_data = _maybe_extract_result_dict(result_text)
        if result_data:
            if result_data.get("game_over"):
                logger.info("[pilot] Game over detected from %s, switching to auto-pass", fn.name)
                if game_log:
                    game_log.emit("auto_pilot_mode", reason="game_over")
                await auto_pass_loop(session, "pilot")
                return True, turn_state.tools_called
            if result_data.get("player_dead"):
                logger.info("[pilot] Player dead detected from %s, switching to auto-pass", fn.name)
                if game_log:
                    game_log.emit("auto_pilot_mode", reason="player_dead")
                await auto_pass_loop(session, "pilot")
                return True, turn_state.tools_called

        display_text = result_text
        if fn.name in ("pass_priority", "get_action_choices", "choose_action"):
            display_text, state.last_board = render_for_pilot(result_text, state.last_board, state.seen_oracle_cards)
            turns_since_chat = state.current_game_turn - state.last_chat_turn
            chat_budget_left = turn_state.chat_messages_this_turn < MAX_CHAT_MESSAGES_PER_TURN
            if turns_since_chat >= 2 and display_text != result_text and chat_budget_left:
                display_text += (
                    f"\n\n[It's been {turns_since_chat} turns since you last "
                    f"chatted — send a message to your opponent!]"
                )

        state.history.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": display_text,
            }
        )

    if not turn_state.had_successful_action and (
        turn_state.had_actionable_opportunity
        or not turn_state.tools_called
        or turn_state.tools_called <= INFO_ONLY_TOOLS
    ):
        state.turns_without_progress += 1
    return False, turn_state.tools_called


def build_initial_message(pass_priority_result: dict) -> str:
    """Build the initial user message from a pass_priority result."""
    if pass_priority_result.get("game_over"):
        return "The game is over."
    if not pass_priority_result.get("action_pending"):
        return "The game is starting. Call pass_priority to get your first decision."

    action_type = pass_priority_result.get("action_type")
    message = pass_priority_result.get("message")

    if message and ("Mulligan" in message or "mulligan" in message.lower()):
        return (
            f"The game is starting. Your first decision: {message}\n"
            "Call get_action_choices to see your hand, then choose_action to decide."
        )
    if action_type:
        return (
            f"The game is starting. Your first decision ({action_type}): {message if message else ''}\n"
            "Call get_action_choices to see your options, then choose_action to decide."
        )
    return "The game is starting. Call pass_priority to get your first decision."


async def _prefetch_first_action(session: ClientSession) -> str:
    """Wait for the first game decision and return a descriptive initial message."""
    result_text = await execute_tool(session, "pass_priority", {})
    try:
        result = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return "The game is starting. Call pass_priority to get your first decision."
    return build_initial_message(result)


async def run_pilot_loop(
    session: ClientSession,
    client: AsyncOpenAI,
    model: str,
    system_prompt: str,
    tools: list[dict],
    prices: dict[str, tuple[float, float]],
    username: str = "",
    game_dir: Path | None = None,
    game_log: GameLogWriter | None = None,
    trace_log: GameLogWriter | None = None,
    reasoning_effort: str = "",
    ignore_providers: list[str] | None = None,
    provider_order: list[str] | None = None,
    cache_control: dict | None = None,
) -> None:
    """Run the LLM-driven game-playing loop."""
    try:
        initial_message = await _prefetch_first_action(session)
    except ToolExecutionError as exc:
        _record_tool_execution_failure(exc, username, game_dir, game_log)
        raise
    state = PilotLoopState(history=[{"role": "user", "content": initial_message}])
    model_price = get_model_price(model, prices)
    game_start = time.monotonic()

    while True:
        if time.monotonic() - game_start > MAX_GAME_DURATION_SECS:
            logger.warning("[pilot] Maximum game duration exceeded, switching to auto-pass")
            if game_log:
                game_log.emit("auto_pilot_mode", reason="max_duration_exceeded")
            await auto_pass_loop(session, "pilot")
            return
        try:
            messages = await _build_loop_messages(state, session, system_prompt, cache_control)
            _mark_tail_cache_breakpoint(messages, state, cache_control)

            create_kwargs: dict = {
                "model": model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
                "max_tokens": MAX_TOKENS,
            }
            extra_body: dict = {}
            if reasoning_effort:
                extra_body["reasoning"] = {"effort": reasoning_effort}
            if ignore_providers or provider_order:
                provider_cfg: dict = {}
                if ignore_providers:
                    provider_cfg["ignore"] = ignore_providers
                if provider_order:
                    provider_cfg["order"] = provider_order
                extra_body["provider"] = provider_cfg
            if extra_body:
                create_kwargs["extra_body"] = extra_body
            response = await asyncio.wait_for(
                client.chat.completions.create(**create_kwargs),
                timeout=LLM_REQUEST_TIMEOUT_SECS,
            )
            state.consecutive_timeouts = 0
            if not response.choices:
                state.consecutive_empty_choices += 1
                logger.warning(
                    "[pilot] LLM returned empty/null choices, retrying... [%d]",
                    state.consecutive_empty_choices,
                )
                if state.consecutive_empty_choices >= MAX_CONSECUTIVE_EMPTY_CHOICES:
                    logger.warning("[pilot] LLM returning empty choices repeatedly, switching to auto-pass mode")
                    if game_log:
                        game_log.emit(
                            "auto_pilot_mode",
                            reason=f"LLM degraded ({state.consecutive_empty_choices} consecutive empty choices)",
                        )
                    try:
                        await execute_tool(
                            session,
                            "send_chat_message",
                            {"message": "My brain is fried... going on autopilot for the rest of this game. GG!"},
                        )
                    except ToolExecutionError:
                        pass
                    await auto_pass_loop(session, "pilot")
                    return
                continue
            state.consecutive_empty_choices = 0
            choice = response.choices[0]
            if _handle_truncated_response(state, choice, response, game_log):
                continue

            if trace_log:
                trace_log.emit(
                    "llm_call",
                    request=create_kwargs,
                    response=response.model_dump(),
                )

            call_cost = 0.0
            if response.usage and model_price is not None:
                input_cost = (response.usage.prompt_tokens or 0) * model_price[0] / 1_000_000
                output_cost = (response.usage.completion_tokens or 0) * model_price[1] / 1_000_000
                call_cost = input_cost + output_cost
                state.cumulative_cost += call_cost
                if game_dir:
                    write_cost_file(game_dir, username, state.cumulative_cost)

            if game_log:
                llm_event = {"reasoning": choice.message.content}
                thinking = getattr(choice.message, "reasoning_content", None)
                if thinking:
                    llm_event["thinking"] = thinking
                if choice.message.tool_calls:
                    llm_event["tool_calls"] = [
                        {"name": tool_call.function.name, "arguments": tool_call.function.arguments}
                        for tool_call in choice.message.tool_calls
                    ]
                if response.usage:
                    usage_dict: dict = {
                        "prompt_tokens": response.usage.prompt_tokens or 0,
                        "completion_tokens": response.usage.completion_tokens or 0,
                    }
                    prompt_details = response.usage.prompt_tokens_details
                    if prompt_details and getattr(prompt_details, "cached_tokens", None):
                        usage_dict["cached_tokens"] = prompt_details.cached_tokens
                        total_prompt = response.usage.prompt_tokens or 0
                        if prompt_details.cached_tokens > total_prompt > 0:
                            logger.warning(
                                "[pilot] cached_tokens (%d) > prompt_tokens (%d) — upstream API bug",
                                prompt_details.cached_tokens,
                                total_prompt,
                            )
                        elif total_prompt > 0:
                            hit_pct = prompt_details.cached_tokens / total_prompt * 100
                            logger.debug(
                                "[pilot] Cache: %d/%d (%.0f%%)", prompt_details.cached_tokens, total_prompt, hit_pct
                            )
                    completion_details = response.usage.completion_tokens_details
                    if completion_details and getattr(completion_details, "reasoning_tokens", None):
                        usage_dict["reasoning_tokens"] = completion_details.reasoning_tokens
                    llm_event["usage"] = usage_dict
                llm_event["cost_usd"] = round(call_cost, 6)
                llm_event["cumulative_cost_usd"] = round(state.cumulative_cost, 6)
                if state.last_game_seq is not None:
                    llm_event["game_seq"] = state.last_game_seq
                game_log.emit("llm_response", **llm_event)

            turn_tools_called: set[str] = set()
            if choice.message.tool_calls:
                finished, turn_tools_called = await _process_tool_calls(
                    session,
                    choice,
                    state,
                    username,
                    game_dir,
                    game_log,
                )
                if finished:
                    return
            else:
                state.turns_without_progress += 1
                content = choice.message.content
                if content:
                    content = content.strip()
                if content:
                    logger.info("[pilot] Thinking: %s", content[:500])
                    state.history.append({"role": "assistant", "content": content})
                    state.empty_responses = 0
                    state.last_was_empty = False
                elif not state.last_was_empty:
                    logger.warning("[pilot] Empty response from LLM, retrying...")
                    state.last_was_empty = True
                    continue
                else:
                    state.last_was_empty = False
                    state.empty_responses += 1
                    logger.warning("[pilot] Empty response from LLM (no tools, no text) [%d]", state.empty_responses)
                    if state.empty_responses >= MAX_EMPTY_RESPONSES:
                        logger.warning("[pilot] LLM appears degraded (no tools or text), switching to auto-pass mode")
                        if game_log:
                            game_log.emit("auto_pilot_mode", reason="LLM degraded (10+ empty responses)")
                        try:
                            await execute_tool(
                                session,
                                "send_chat_message",
                                {"message": "My brain is fried... going on autopilot for the rest of this game. GG!"},
                            )
                        except ToolExecutionError:
                            pass
                        await auto_pass_loop(session, "pilot")
                        return
                state.history.append(
                    {
                        "role": "user",
                        "content": "Continue playing. Call pass_priority.",
                    }
                )

            if state.turns_without_progress >= MAX_TURNS_WITHOUT_PROGRESS:
                await _recover_from_stall(
                    session,
                    state,
                    game_log,
                    turn_tools_called,
                )
                continue

        except TimeoutError:
            await _handle_timeout(session, state, game_log)

        except ToolExecutionError as exc:
            _record_tool_execution_failure(exc, username, game_dir, game_log)
            raise

        except OpenAIError as exc:
            state.consecutive_timeouts = 0
            error_str = str(exc)
            logger.warning("[pilot] LLM error: %s", exc)
            if game_log:
                game_log.emit("llm_error", error_type=type(exc).__name__, error_message=error_str[:500])

            reason = _classify_permanent_llm_failure(error_str)
            if reason is not None:
                logger.warning("[pilot] %s, aborting", reason)
                if game_log:
                    game_log.emit("permanent_llm_failure", reason=reason)
                try:
                    await execute_tool(
                        session,
                        "send_chat_message",
                        {"message": f"{reason}... aborting game. GG!"},
                    )
                except ToolExecutionError:
                    pass
                raise PermanentLLMError(reason) from None

            try:
                await execute_tool(session, "pass_priority", {})
            except ToolExecutionError:
                await asyncio.sleep(5)

            reset_context(
                state,
                "Continue playing. Call pass_priority.",
                reset_board_context=False,
            )


async def run_pilot(
    server: str,
    port: int,
    username: str,
    project_root: Path,
    prices: dict[str, tuple[float, float]],
    deck_path: Path | None = None,
    api_key: str = "",
    model: str = DEFAULT_MODEL,
    provider: str = DEFAULT_LLM_PROVIDER,
    system_prompt: str = "",
    game_dir: Path | None = None,
    max_interactions_per_turn: int | None = None,
    reasoning_effort: str = "",
    tools: set[str] | None = None,
    ignore_providers: list[str] | None = None,
    provider_order: list[str] | None = None,
    cache_control: dict | None = None,
) -> None:
    """Run the pilot client."""
    base_url = llm_base_url(provider)
    logger.info("[pilot] Starting for %s@%s:%s", username, server, port)
    logger.info("[pilot] Model: %s", model)
    logger.info("[pilot] Provider: %s", provider)
    if reasoning_effort:
        logger.info("[pilot] Reasoning effort: %s", reasoning_effort)
    if tools is not None:
        logger.info("[pilot] Custom toolset: %s", sorted(tools))
    if ignore_providers:
        logger.info("[pilot] Ignoring providers: %s", ignore_providers)
    if provider_order:
        logger.info("[pilot] Provider order: %s", provider_order)
    if cache_control:
        logger.debug("[pilot] Prompt cache_control: %s", cache_control)
    if provider != DEFAULT_LLM_PROVIDER:
        assert ignore_providers is None, (
            f"ignore_providers requires provider={DEFAULT_LLM_PROVIDER!r}, got {provider!r}"
        )
        assert provider_order is None, f"provider_order requires provider={DEFAULT_LLM_PROVIDER!r}, got {provider!r}"

    llm_client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=LLM_REQUEST_TIMEOUT_SECS + 5,
        max_retries=1,
    )

    launch_args = build_bridge_launch_args(
        server=server,
        port=port,
        username=username,
        deck_path=deck_path,
        heap_size_mb=512,
        error_log_path=game_dir / f"{username}_errors.log" if game_dir else None,
        bridge_log_path=game_dir / f"{username}_bridge.jsonl" if game_dir else None,
        max_interactions_per_turn=max_interactions_per_turn,
    )

    logger.info("[pilot] Spawning bridge client...")

    game_log = None
    trace_log = None
    with ExitStack() as log_stack:
        if game_dir:
            game_log = log_stack.enter_context(GameLogWriter(game_dir, username))
            trace_log = log_stack.enter_context(GameLogWriter(game_dir, username, suffix="llm_trace"))

        try:
            async with spawn_bridge_http(
                mvn_args=launch_args.mvn_args,
                project_root=project_root,
                jvm_args=launch_args.jvm_args,
                log_file=game_dir / f"{username}_mcp.log" if game_dir else None,
            ) as session:
                result = await session.initialize()
                logger.debug("[pilot] MCP initialized: %s", result.serverInfo)

                tools_result = await session.list_tools()
                if tools is not None:
                    available_mcp_names = {tool.name for tool in tools_result.tools}
                    unknown = tools - available_mcp_names
                    if unknown:
                        raise ValueError(
                            f"Toolset references unknown MCP tools: {sorted(unknown)}. "
                            f"Available: {sorted(available_mcp_names)}"
                        )
                openai_tools = mcp_tools_to_openai(tools_result.tools, tools)
                tool_names = [tool["function"]["name"] for tool in openai_tools]
                logger.debug("[pilot] Available tools: %s", tool_names)

                if game_log:
                    game_log.emit(
                        "game_start",
                        model=model,
                        system_prompt=system_prompt,
                        available_tools=tool_names,
                        deck_path=str(deck_path) if deck_path else None,
                    )

                logger.info("[pilot] Starting game-playing loop...")
                await run_pilot_loop(
                    session,
                    llm_client,
                    model,
                    system_prompt,
                    openai_tools,
                    username=username,
                    game_dir=game_dir,
                    prices=prices,
                    game_log=game_log,
                    trace_log=trace_log,
                    reasoning_effort=reasoning_effort,
                    ignore_providers=ignore_providers,
                    provider_order=provider_order,
                    cache_control=cache_control,
                )
        finally:
            if game_log:
                game_log.emit("game_end", total_cost_usd=round(game_log.last_cumulative_cost_usd(), 6))


def main() -> int:
    """Main entry point."""
    setup_logging()
    parser = argparse.ArgumentParser(description="Pilot LLM game player for XMage")
    parser.add_argument("--server", default="localhost", help="XMage server address")
    parser.add_argument("--port", type=int, default=17171, help="XMage server port")
    parser.add_argument("--username", default="Pilot", help="Player username")
    parser.add_argument("--project-root", type=Path, help="Project root directory")
    parser.add_argument("--deck", type=Path, help="Path to deck file (.dck)")
    parser.add_argument("--api-key", default="", help="API key (prefer provider-specific env vars)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"LLM model (default: {DEFAULT_MODEL})")
    parser.add_argument("--provider", choices=SUPPORTED_LLM_PROVIDERS, default=DEFAULT_LLM_PROVIDER)
    parser.add_argument("--system-prompt", default="", help="Custom system prompt")
    parser.add_argument("--game-dir", type=Path, help="Game directory for cost file output")
    parser.add_argument("--max-interactions-per-turn", type=int, help="Loop detection threshold (default 25)")
    parser.add_argument("--reasoning-effort", default="", help="OpenRouter reasoning effort: low, medium, high")
    parser.add_argument("--tools", default="", help="Comma-separated MCP tool names (default: all)")
    parser.add_argument("--ignore-providers", default="", help="Comma-separated OpenRouter providers to exclude")
    parser.add_argument("--provider-order", default="", help="Comma-separated OpenRouter providers to prefer, in order")
    parser.add_argument("--cache-control", default="", help="JSON cache_control config for prompt caching")
    args = parser.parse_args()

    if args.project_root:
        project_root = args.project_root.resolve()
    else:
        project_root = Path.cwd().resolve()
        if project_root.name == "puppeteer" and project_root.parent.name == "src":
            project_root = project_root.parent.parent.parent
        elif project_root.name == "puppeteer":
            project_root = project_root.parent

    provider = args.provider or DEFAULT_LLM_PROVIDER

    api_key = args.api_key
    if not api_key.strip():
        required_key_env = required_api_key_env(provider)
        api_key = os.environ.get(required_key_env)
    if not api_key or not api_key.strip():
        logger.error("[pilot] Missing API key for provider %s", provider)
        logger.error("[pilot] Pass --api-key or export the provider's configured API key env var.")
        return 2

    prices = load_prices()
    logger.debug("[pilot] Project root: %s", project_root)

    system_prompt = args.system_prompt or _load_default_system_prompt()

    pilot_tools = set(args.tools.split(",")) if args.tools else None
    ignore_providers = args.ignore_providers.split(",") if args.ignore_providers else None
    provider_order = args.provider_order.split(",") if args.provider_order else None
    cache_control = json.loads(args.cache_control) if args.cache_control else None
    if provider != DEFAULT_LLM_PROVIDER:
        if ignore_providers:
            logger.error("[pilot] --ignore-providers requires --provider=%s", DEFAULT_LLM_PROVIDER)
            return 2
        if provider_order:
            logger.error("[pilot] --provider-order requires --provider=%s", DEFAULT_LLM_PROVIDER)
            return 2

    try:
        asyncio.run(
            run_pilot(
                server=args.server,
                port=args.port,
                username=args.username,
                project_root=project_root,
                deck_path=args.deck,
                api_key=api_key,
                model=args.model,
                provider=args.provider,
                system_prompt=system_prompt,
                game_dir=args.game_dir,
                prices=prices,
                max_interactions_per_turn=args.max_interactions_per_turn,
                reasoning_effort=args.reasoning_effort,
                tools=pilot_tools,
                ignore_providers=ignore_providers,
                provider_order=provider_order,
                cache_control=cache_control,
            )
        )
    except KeyboardInterrupt:
        pass
    except PermanentLLMError as exc:
        logger.error("[pilot] Permanent LLM failure: %s", exc)
        return PERMANENT_FAILURE_EXIT_CODE

    return 0


if __name__ == "__main__":
    sys.exit(main())
