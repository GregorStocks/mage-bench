"""Pilot: LLM-powered game player that makes strategic decisions via MCP tools."""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from mcp import ClientSession
from mcp.types import Tool
from openai import AsyncOpenAI, OpenAIError

from puppeteer.auto_pass import auto_pass_loop
from puppeteer.bridge_transport import build_bridge_launch_args, spawn_bridge_http
from puppeteer.config import load_prompts
from puppeteer.decision_renderer import BASIC_LAND_NAMES, render_decision
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
from puppeteer.tool_error import ToolExecutionError, extract_text_content
from schemas.game_export_types import Decision

logger = get_logger(__name__)

DEFAULT_MODEL = "google/gemini-2.0-flash-001"

# Exit code returned when the LLM permanently fails (404 model not found,
# 402/403 credits exhausted).  The orchestrator checks for this to abort the
# game early instead of wasting API tokens on the other player.
PERMANENT_FAILURE_EXIT_CODE = 3


class PermanentLLMError(Exception):
    """Raised when the LLM is permanently unreachable (model not found, credits exhausted)."""


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

# Context window management.
# History is append-only; before each LLM call we render a bounded context
# window from history: recent messages at full fidelity, older messages
# with tool results summarised to save tokens.
CONTEXT_RECENT_COUNT = 40  # recent history entries kept at full fidelity
CONTEXT_SUMMARY_COUNT = 20  # older entries included as compact summaries
TOOL_SUMMARY_TRIGGER_CHARS = 200  # tool messages longer than this enter the summary path
RENDER_INTERVAL = 5  # re-render context every N iterations when history is long
MAX_CHAT_MESSAGES_PER_TURN = 2  # max send_chat_message calls per LLM iteration


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


def _extract_oracle_texts_from_board(board: list[dict]) -> dict[str, dict]:
    """Extract oracle text from board payload's rules fields.

    The bridge includes `rules` on every card (hand, battlefield, etc.).
    Convert these to the oracle_texts format expected by render_decision().
    """
    oracle_texts: dict[str, dict] = {}
    for player in board:
        for zone in ("hand", "battlefield", "graveyard", "exile", "commanders"):
            zone_cards = player.get(zone)
            if zone_cards is None:
                continue
            for card in zone_cards:
                if not isinstance(card, dict):
                    continue
                name = card["name"]
                if not name or name in BASIC_LAND_NAMES or name in oracle_texts:
                    continue
                rules = card.get("rules")
                if not rules:
                    continue
                entry: dict[str, str] = {}
                if card.get("mana_cost"):
                    entry["mana_cost"] = card["mana_cost"]
                # Build type_line from available info
                if card.get("is_land"):
                    entry["type_line"] = "Land"
                if card.get("power") is not None:
                    entry["power_toughness"] = f"{card['power']}/{card['toughness']}"
                if rules:
                    entry["oracle_text"] = " / ".join(rules)
                oracle_texts[name] = entry
    return oracle_texts


def _build_pilot_snapshot(data: dict, board: list[dict] | None) -> dict:
    """Build a snapshot-like dict from a pass_priority/get_action_choices result.

    Converts the flat board (list of players) into the snapshot format
    expected by render_decision().
    """
    players: list[dict] = []
    if board:
        for p in board:
            player: dict = {
                "name": p.get("name", "?"),
                "life": p.get("life", 0),
                "library_size": p.get("library_size"),
            }
            if p.get("hand"):
                player["hand"] = p["hand"]
            hand = p.get("hand")
            player["hand_count"] = p.get("hand_size", len(hand) if hand is not None else 0)
            for zone in ("battlefield", "graveyard", "exile", "commanders"):
                if p.get(zone):
                    player[zone] = p[zone]
            if p.get("counters"):
                player["counters"] = p["counters"]
            players.append(player)

    snapshot: dict = {"players": players}
    if data.get("stack"):
        snapshot["stack"] = data["stack"]
    if data.get("combat"):
        snapshot["combat"] = data["combat"]
    return snapshot


def _build_pilot_decision(data: dict) -> Decision:
    """Build a decision-like dict from a pass_priority/get_action_choices result.

    Extracts the fields that render_decision() reads from a decision.
    """
    choices = data.get("choices")
    if choices is None:
        choices = []
    action_type = data.get("action_type")
    response_type = data.get("response_type")
    message = data.get("message")
    assert action_type is None or isinstance(action_type, str), (
        f"action_type must be a string when present, got {action_type!r}"
    )
    assert response_type is None or isinstance(response_type, str), (
        f"response_type must be a string when present, got {response_type!r}"
    )
    assert message is None or isinstance(message, str), f"message must be a string when present, got {message!r}"
    decision = Decision(
        index=0,
        snapshotIndex=0,
        player="You",
        turn=0,
        phase="",
        actionType="" if action_type is None else action_type,
        responseType="" if response_type is None else response_type,
        message="" if message is None else message,
        choices=choices,
        choiceCount=len(choices),
        isForced=len(choices) <= 1,
        llmEventIndices=[],
        subsequentActions=[],
    )

    # Parse context string for turn/phase: "T3 Precombat Main/Precombat Main (Alice) YOUR_MAIN"
    context = data.get("context")
    if context:
        m = re.match(r"T(\d+)\s+(.+?)(?:\s+\(|$)", context)
        if m:
            decision.turn = int(m.group(1))
            decision.phase = m.group(2).split("/")[0].strip().upper().replace(" ", "_")

    # Find player name from board
    board = data.get("board")
    if isinstance(board, list):
        for p in board:
            if isinstance(p, dict) and p.get("is_you"):
                decision.player = p["name"]
                break

    # Pilot context overlay
    pilot_ctx: dict = {}
    if "untapped_lands" in data:
        pilot_ctx["untappedLands"] = data["untapped_lands"]
    if "land_drops_used" in data:
        pilot_ctx["landDropsUsed"] = data["land_drops_used"]
    if "combat_phase" in data:
        pilot_ctx["combatPhase"] = data["combat_phase"]
    if "already_attacking" in data:
        pilot_ctx["alreadyAttacking"] = data["already_attacking"]
    if "incoming_attackers" in data:
        pilot_ctx["incomingAttackers"] = data["incoming_attackers"]
    if "mana_pool" in data:
        pilot_ctx["manaPool"] = data["mana_pool"]
    if pilot_ctx:
        decision.pilotContext = pilot_ctx

    # Multi-amount items (e.g. combat damage distribution targets)
    items = data.get("items")
    if items:
        decision.items = items
        if "total_min" in data:
            decision.totalMin = data["total_min"]
        if "total_max" in data:
            decision.totalMax = data["total_max"]

    return decision


def _render_for_pilot(
    result_text: str,
    last_board: list[dict] | None,
    seen_oracle_cards: set[str] | None = None,
) -> tuple[str, list[dict] | None]:
    """Render an action result for LLM consumption.

    Handles pass_priority, get_action_choices, and choose_action results.
    Returns (rendered_text, updated_board). The board is tracked so that
    board_unchanged results can use the last-known board. Results without
    action_pending are passed through as raw JSON.
    """
    try:
        data = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return result_text, last_board

    if not isinstance(data, dict) or not data.get("action_pending"):
        # Not a decision — pass through the raw JSON
        return result_text, last_board

    # Get board (current or last-known)
    board = data.get("board")
    if isinstance(board, list):
        last_board = board
    elif board is None:
        board = last_board
    else:
        board = last_board

    snapshot = _build_pilot_snapshot(data, board)
    decision = _build_pilot_decision(data)

    # Extract oracle texts from board's rules fields, filtering out
    # cards whose oracle text was already shown in a previous message.
    oracle_texts = _extract_oracle_texts_from_board(board) if board else {}
    if seen_oracle_cards is not None:
        oracle_texts = {k: v for k, v in oracle_texts.items() if k not in seen_oracle_cards}
        seen_oracle_cards.update(oracle_texts)

    deciding_player = decision.player

    rendered = render_decision(
        decision,
        snapshot,
        oracle_texts=oracle_texts,
        deciding_player=deciding_player,
        include_card_reference=True,
    )

    # Append operational metadata the LLM needs to respond
    lines = [rendered]
    resp_type = data.get("response_type")
    respond_with = data.get("respond_with")
    if respond_with:
        # When total_min == total_max, the Items header shows "total=N" instead
        # of "total_min=N, total_max=N", so adjust the respond_with text to match.
        total_min = data.get("total_min")
        total_max = data.get("total_max")
        if total_min is not None and total_max is not None and total_min == total_max:
            respond_with = respond_with.replace(
                "sum between total_min and total_max",
                f"sum must equal total ({total_min})",
            )
        lines.append(f"  Respond: {respond_with}")
    elif resp_type:
        lines.append(f"  Response type: {resp_type}")

    mana_pool = data.get("mana_pool")
    if mana_pool and any(v > 0 for v in mana_pool.values()):
        pool_str = ", ".join(f"{k}={v}" for k, v in mana_pool.items() if v > 0)
        lines.append(f"  Mana pool: {pool_str}")

    recent_chat = data.get("recent_chat")
    if recent_chat:
        lines.append("  Recent chat: " + " | ".join(recent_chat))

    return "\n".join(lines), last_board


def _summarize_tool_result(tool_name: str, content: str) -> str:
    """Compress a tool result to a short summary for older context entries.

    Parses the JSON result and extracts key fields per tool type.
    Falls back to the original content for unknown tools or invalid JSON.
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content

    if tool_name == "pass_priority":
        if data.get("player_dead"):
            return "player_dead"
        if data.get("action_pending"):
            stop = data.get("stop_reason")
            action_type = data.get("action_type", "?")
            parts = []
            if stop:
                parts.append(f"action_pending({action_type}, {stop})")
            else:
                parts.append(f"action_pending({action_type})")
            # Include choice summary (pass_priority now returns choices inline)
            resp_type = data.get("response_type")
            if resp_type:
                parts.append(resp_type)
            choices = data.get("choices")
            if choices:
                names = [c.get("name", c.get("description", "?"))[:30] for c in choices[:3]]
                parts.append(f"{len(choices)} choices: {', '.join(names)}")
            msg = data.get("message")
            if msg and not choices:
                parts.append(msg[:60])
            return "; ".join(parts)
        stop = data.get("stop_reason")
        if isinstance(stop, str) and stop:
            return stop
        return "passed"

    if tool_name == "choose_action":
        if data.get("success"):
            summary = f"OK: {data.get('action_taken', '?')}"
            if data.get("mana_plan_set"):
                summary += f" (mana_plan: {data.get('mana_plan_size', '?')} entries)"
            return summary
        return f"FAIL: {data.get('error', '?')[:100]}"

    if tool_name == "get_action_choices":
        parts = [data.get("action_type", "?")]
        resp_type = data.get("response_type")
        if resp_type:
            parts.append(resp_type)
        choices = data.get("choices")
        if choices:
            names = [c.get("name", c.get("description", "?"))[:30] for c in choices[:3]]
            parts.append(f"{len(choices)} choices: {', '.join(names)}")
        msg = data.get("message")
        if msg and not choices:
            parts.append(msg[:60])
        return "; ".join(parts)

    if tool_name == "get_game_state":
        parts = []
        if "turn" in data:
            parts.append(f"T{data['turn']}")
        if "phase" in data:
            parts.append(data["phase"])
        players = data.get("players")
        if players is not None:
            for p in players:
                name = p.get("name", "?")
                life = p.get("life", "?")
                bf_zone = p.get("battlefield")
                bf = len(bf_zone) if bf_zone is not None else 0
                parts.append(f"{name}:{life}hp/{bf}perm")
        return "; ".join(parts) if parts else content

    if tool_name == "get_game_log":
        total = data.get("total_length", "?")
        truncated = data.get("truncated", False)
        since = data.get("since_turn")
        log_text = data.get("log")
        prefix = f"log({total} chars"
        if since is not None:
            prefix += f", since_turn={since}"
        if truncated:
            prefix += ", truncated"
        prefix += "): "
        if log_text:
            lines = [line.strip() for line in log_text.splitlines() if line.strip()]
            if lines:
                excerpt = " / ".join(lines[:4])
                if len(lines) > 4:
                    excerpt += " / ..."
                return prefix + excerpt
        return prefix.rstrip(": ")

    # get_oracle_text, send_chat_message, unknown
    return content


def _find_tool_name(history: list[dict], tool_result_idx: int, tool_call_id: str) -> str:
    """Find the tool name for a tool result by searching backward for its assistant message."""
    for j in range(tool_result_idx - 1, -1, -1):
        msg = history[j]
        if msg.get("role") == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls is None:
                continue
            for tc in tool_calls:
                if tc.get("id") == tool_call_id:
                    function = tc.get("function")
                    assert isinstance(function, dict), (
                        f"assistant tool call {tool_call_id!r} missing function payload: {tc!r}"
                    )
                    name = function.get("name")
                    assert isinstance(name, str), f"assistant tool call {tool_call_id!r} missing function name: {tc!r}"
                    return name
            break
    return ""


def _extract_last_reasoning(history: list[dict]) -> str:
    """Extract the last assistant reasoning text from history (for context resets)."""
    for msg in reversed(history):
        if msg.get("role") == "assistant":
            content = msg.get("content")
            if isinstance(content, str) and content:
                return content[:300]
    return ""


def _build_reset_message(
    base_text: str,
    last_reasoning: str,
) -> str:
    """Build the user message for a context reset."""
    parts = [base_text]
    if last_reasoning:
        parts.append(f"Before your context was reset, you were thinking: {last_reasoning}")
    return "\n\n".join(parts)


def _with_cache_control(msg: dict, cache_control: dict) -> dict:
    """Return a copy of msg with cache_control added to its content.

    Converts string content to content-block format with cache_control attached.
    Works for user, assistant (with text content), and tool messages.
    Returns the message unchanged if the content isn't suitable (e.g. None/empty).
    """
    role = msg["role"]
    content = msg.get("content")

    if role in ("user", "tool"):
        if isinstance(content, str):
            return {**msg, "content": [{"type": "text", "text": content, "cache_control": cache_control}]}
        if isinstance(content, list):
            new_content = [dict(block) for block in content]
            for block in reversed(new_content):
                if isinstance(block, dict) and block.get("type") == "text":
                    block["cache_control"] = cache_control
                    break
            return {**msg, "content": new_content}
    elif role == "assistant" and isinstance(content, str) and content:
        return {**msg, "content": [{"type": "text", "text": content, "cache_control": cache_control}]}

    return msg


_CACHE_BREAKPOINT_MARKER = "All cards listed are playable right now."


def _message_text(msg: dict) -> str:
    """Extract concatenated text content from a rendered message."""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block["text"] for block in content if isinstance(block, dict) and block.get("type") == "text")
    return ""


def _find_cache_breakpoint_idx(messages: list[dict]) -> int:
    """Return the message index that ends the stable cacheable prefix."""
    for idx in range(len(messages) - 1, -1, -1):
        if messages[idx].get("role") == "user" and _CACHE_BREAKPOINT_MARKER in _message_text(messages[idx]):
            return idx
    return len(messages) - 1


def _render_context(
    history: list[dict],
    system_prompt: str,
    state_summary: str,
    cache_control: dict | None = None,
) -> list[dict]:
    """Build the LLM messages list from append-only history.

    Recent messages (last CONTEXT_RECENT_COUNT) are included at full fidelity.
    Older messages (up to CONTEXT_SUMMARY_COUNT before the recent window) have
    their tool results summarised to save tokens. Everything older is dropped.
    """
    messages: list[dict]
    if cache_control:
        # Use content block format with cache_control for providers that need it
        # (e.g. Anthropic via OpenRouter)
        messages = [
            {
                "role": "system",
                "content": [{"type": "text", "text": system_prompt, "cache_control": cache_control}],
            }
        ]
    else:
        messages = [{"role": "system", "content": system_prompt}]

    if len(history) <= CONTEXT_RECENT_COUNT:
        # Short history — include everything at full fidelity
        messages.extend(history)
        return messages

    # Long history — summarised prefix (cacheable), state bridge, recent slice.
    # State bridge is placed after the summarised section so the prefix
    # (system + summarised) stays stable across iterations for prompt caching.

    # Find a clean boundary for the recent slice — don't split assistant/tool pairs.
    # Walk the recent boundary backward so it doesn't start on a tool message.
    recent_start = len(history) - CONTEXT_RECENT_COUNT
    while recent_start > 0 and history[recent_start].get("role") == "tool":
        recent_start -= 1

    # Summarised older slice
    summary_start = max(0, recent_start - CONTEXT_SUMMARY_COUNT)
    # Same clean-boundary logic for the summary start
    while summary_start > 0 and history[summary_start].get("role") == "tool":
        summary_start -= 1

    for i in range(summary_start, recent_start):
        msg = history[i]
        if msg.get("role") == "tool" and len(msg["content"]) > TOOL_SUMMARY_TRIGGER_CHARS:
            tool_name = _find_tool_name(history, i, msg["tool_call_id"])
            messages.append({**msg, "content": _summarize_tool_result(tool_name, msg["content"])})
        else:
            messages.append(msg)

    # State bridge — after cacheable prefix, before recent window.
    # With cache_control, this marks the end of the cacheable prefix (system +
    # summarised section).  For models with a 4096-token minimum (Opus), the
    # prefix at this point is ~6k tokens — comfortably above the threshold.
    bridge_text = (
        f"{state_summary}"
        "Continue playing. Call pass_priority to get your next decision, "
        "then choose_action to respond. "
        "All cards listed are playable right now. "
        "Play cards with choice=pN, pass with choice=no."
    )
    if cache_control:
        messages.append(
            {
                "role": "user",
                "content": [{"type": "text", "text": bridge_text, "cache_control": cache_control}],
            }
        )
    else:
        messages.append({"role": "user", "content": bridge_text})

    # Recent slice — full fidelity
    messages.extend(history[recent_start:])
    return messages


async def _fetch_state_summary(session: ClientSession) -> str:
    """Fetch a compact game state summary for context bridging."""
    state_result = await execute_tool(session, "get_game_state", {})
    try:
        state_data = json.loads(state_result)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ToolExecutionError(f"get_game_state returned invalid JSON: {state_result!r}") from exc
    if not isinstance(state_data, dict):
        raise ToolExecutionError(f"get_game_state returned non-object payload: {state_data!r}")
    if "error" in state_data:
        raise ToolExecutionError(f"get_game_state returned error: {state_data['error']}")
    parts: list[str] = []
    if "turn" in state_data:
        parts.append(f"Turn {state_data['turn']}")
    if "phase" in state_data:
        parts.append(state_data["phase"])
    for p in state_data["players"]:
        name = p.get("name", "?")
        life = p.get("life", "?")
        bf_zone = p.get("battlefield")
        bf = len(bf_zone) if bf_zone is not None else 0
        hand = p.get("hand_count", p.get("hand_size", "?"))
        parts.append(f"{name}: {life}hp, {bf} permanents, {hand} cards")
    return "Current game state: " + "; ".join(parts) + ". "


# Tools that are purely informational (don't advance game state).
# Used by stall detection to classify LLM turns.
INFO_ONLY_TOOLS = {"get_game_state", "get_oracle_text", "send_chat_message"}


def _load_default_system_prompt() -> str:
    """Load the default system prompt from prompts.json."""
    prompts = load_prompts(None)
    assert "default" in prompts, "prompts.json must contain a 'default' key"
    return prompts["default"]


def mcp_tools_to_openai(mcp_tools: Sequence[Tool], allowed_tools: set[str] | None = None) -> list[dict]:
    """Convert MCP tool definitions to OpenAI function calling format.

    Args:
        mcp_tools: Tool definitions from the MCP session.
        allowed_tools: Set of tool names to include. None means include all.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema or {"type": "object", "properties": {}},
            },
        }
        for tool in mcp_tools
        if allowed_tools is None or tool.name in allowed_tools
    ]


async def execute_tool(session: ClientSession, name: str, arguments: dict) -> str:
    """Route a tool call through the MCP session and return the result text."""
    try:
        result = await session.call_tool(name, arguments)
    except Exception as exc:
        raise ToolExecutionError(f"MCP tool {name} failed: {exc}") from exc
    return extract_text_content(name, result)


_BOARD_CURSOR_TOOLS = frozenset({"pass_priority", "get_action_choices"})


class BoardCursorTracker:
    """Tracks board_cursor across tool calls for board state dedup.

    Injects board_cursor into pass_priority/get_action_choices args so the
    bridge can omit the board payload when it hasn't changed. Extracts the
    cursor from tool results to keep it up to date.
    """

    def __init__(self) -> None:
        self.cursor: int | None = None

    def inject(self, tool_name: str, args: dict) -> None:
        """Inject board_cursor into args if applicable."""
        if tool_name in _BOARD_CURSOR_TOOLS and self.cursor is not None:
            args["board_cursor"] = self.cursor

    def extract(self, result_text: str) -> None:
        """Extract board_cursor from a tool result string."""
        try:
            data = json.loads(result_text)
            if isinstance(data, dict) and "board_cursor" in data:
                self.cursor = data["board_cursor"]
        except (json.JSONDecodeError, TypeError):
            pass

    def reset(self) -> None:
        """Force full board on the next call (e.g. after context reset)."""
        self.cursor = None


@dataclass
class PilotLoopState:
    """Mutable state for the pilot loop."""

    history: list[dict]
    state_summary: str = ""
    cumulative_cost: float = 0.0
    empty_responses: int = 0
    last_was_empty: bool = False
    consecutive_timeouts: int = 0
    consecutive_empty_choices: int = 0
    turns_without_progress: int = 0
    consecutive_pass_errors: int = 0
    last_pass_error_msg: str = ""
    consecutive_truncations: int = 0
    consecutive_empty_errors: int = 0
    last_game_seq: int | None = None
    board_tracker: BoardCursorTracker = field(default_factory=BoardCursorTracker)
    last_board: list[dict] | None = None
    current_game_turn: int = 0
    last_chat_turn: int = 0
    seen_oracle_cards: set[str] = field(default_factory=set)
    cache_breakpoint_idx: int | None = None
    render_counter: int = 0


@dataclass
class PilotTurnState:
    """Per-response tool execution state used for stall detection."""

    had_successful_action: bool = False
    had_actionable_opportunity: bool = False
    tools_called: set[str] = field(default_factory=set)
    chat_messages_this_turn: int = 0


def _reset_render_cache(state: PilotLoopState) -> None:
    """Drop cached prompt metadata after a context reset."""
    state.state_summary = ""
    state.cache_breakpoint_idx = None
    state.render_counter = 0


def _reset_context(
    state: PilotLoopState,
    base_text: str,
    *,
    reset_board_context: bool,
) -> None:
    """Reset the conversation while preserving the last assistant reasoning."""
    last_reasoning = _extract_last_reasoning(state.history)
    state.history = [
        {
            "role": "user",
            "content": _build_reset_message(base_text, last_reasoning),
        },
    ]
    _reset_render_cache(state)
    state.seen_oracle_cards.clear()
    if reset_board_context:
        state.board_tracker.reset()
        state.last_board = None


async def _build_loop_messages(
    state: PilotLoopState,
    session: ClientSession,
    system_prompt: str,
    cache_control: dict | None,
) -> list[dict]:
    """Render the next LLM request from the current history.

    Reusing an old full render can leave the recent window stale even when the
    history has grown. Refreshing the rendered messages each turn keeps the
    assistant/tool transcript aligned with the current history while still
    reusing the cheaper state summary between refreshes.
    """
    if len(state.history) > CONTEXT_RECENT_COUNT:
        state.render_counter += 1
        if not state.state_summary or state.render_counter % RENDER_INTERVAL == 0:
            state.state_summary = await _fetch_state_summary(session)
            state.render_counter = 0
        messages = _render_context(state.history, system_prompt, state.state_summary, cache_control)
        state.cache_breakpoint_idx = _find_cache_breakpoint_idx(messages)
        return messages

    messages = _render_context(state.history, system_prompt, state.state_summary, cache_control)
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
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in message.tool_calls
        ]
    return assistant_msg


def _maybe_extract_result_dict(result_text: str) -> dict | None:
    """Parse a JSON tool result when it is a dict."""
    try:
        data = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _handle_truncated_response(
    state: PilotLoopState,
    choice: _ChoiceLike,
    response: _ResponseLike,
    game_log: GameLogWriter | None,
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
        MAX_TOKENS,
        state.consecutive_truncations,
    )
    if state.consecutive_truncations < MAX_CONSECUTIVE_TRUNCATIONS:
        return False

    logger.warning("[pilot] Repeated truncations, resetting conversation context")
    if game_log:
        game_log.emit("context_reset", reason="repeated_truncations")
    _reset_context(
        state,
        "Continue playing. Be concise. Call pass_priority.",
        reset_board_context=True,
    )
    state.consecutive_truncations = 0
    return True


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
            result_text = await execute_tool(session, fn.name, args)
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
            display_text, state.last_board = _render_for_pilot(result_text, state.last_board, state.seen_oracle_cards)
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


async def _recover_from_stall(
    session: ClientSession,
    state: PilotLoopState,
    game_log: GameLogWriter | None,
    turn_tools_called: set[str],
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
    except ToolExecutionError as e:
        logger.warning("[pilot] Auto-pass failed: %s", e)

    state.turns_without_progress = 0
    _reset_context(
        state,
        "A new turn has started. Call pass_priority to continue.",
        reset_board_context=False,
    )


async def _handle_timeout(
    session: ClientSession,
    state: PilotLoopState,
    game_log: GameLogWriter | None,
) -> None:
    """Keep the game moving across request timeouts and reset repeated failures."""
    state.consecutive_timeouts += 1
    logger.warning(
        "[pilot] LLM request timed out after %ss [%d]",
        LLM_REQUEST_TIMEOUT_SECS,
        state.consecutive_timeouts,
    )
    if game_log:
        game_log.emit(
            "llm_error",
            error_type="timeout",
            error_message=f"Timed out after {LLM_REQUEST_TIMEOUT_SECS}s [{state.consecutive_timeouts}]",
        )
    try:
        await execute_tool(session, "pass_priority", {})
    except ToolExecutionError:
        await asyncio.sleep(5)

    if state.consecutive_timeouts < MAX_CONSECUTIVE_TIMEOUTS:
        return

    logger.warning("[pilot] Repeated LLM timeouts, resetting conversation context")
    if game_log:
        game_log.emit("context_reset", reason="repeated_timeouts")
    _reset_context(
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


def build_initial_message(pass_priority_result: dict) -> str:
    """Build the initial user message from a pass_priority result.

    Used both by the real pilot loop (via _prefetch_first_action) and by
    golden prompt tests.
    """
    if pass_priority_result.get("game_over"):
        return "The game is over."
    if not pass_priority_result.get("action_pending"):
        return "The game is starting. Call pass_priority to get your first decision."

    action_type = pass_priority_result.get("action_type")
    message = pass_priority_result.get("message")

    if message and ("Mulligan" in message or "mulligan" in message.lower()):
        return (
            f"The game is starting. Your first decision: {message}\n"
            f"Call get_action_choices to see your hand, then choose_action to decide."
        )
    if action_type:
        return (
            f"The game is starting. Your first decision ({action_type}): {message if message else ''}\n"
            f"Call get_action_choices to see your options, then choose_action to decide."
        )
    return "The game is starting. Call pass_priority to get your first decision."


async def _prefetch_first_action(session: ClientSession) -> str:
    """Wait for the first game decision and return a descriptive initial message.

    Calls pass_priority() which blocks until a decision arrives (e.g.
    mulligan, choose play/draw). Since pass_priority returns choices inline,
    we extract action_type and message directly — no separate get_action_choices
    round-trip needed.
    """
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
    # Pre-fetch the first decision so the LLM knows what it's deciding
    # instead of blindly calling pass_priority with confusing yield params.
    initial_message = await _prefetch_first_action(session)
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

            # Log full LLM request/response to trace file
            if trace_log:
                trace_log.emit(
                    "llm_call",
                    request=create_kwargs,
                    response=response.model_dump(),
                )

            # Track token usage and cost
            call_cost = 0.0
            if response.usage and model_price is not None:
                input_cost = (response.usage.prompt_tokens or 0) * model_price[0] / 1_000_000
                output_cost = (response.usage.completion_tokens or 0) * model_price[1] / 1_000_000
                call_cost = input_cost + output_cost
                state.cumulative_cost += call_cost
                if game_dir:
                    write_cost_file(game_dir, username, state.cumulative_cost)

            # Log LLM response to JSONL
            if game_log:
                llm_event = {"reasoning": choice.message.content}
                # Capture extended thinking / chain-of-thought if present.
                # OpenRouter returns this as `reasoning_content` for models
                # that support it (Claude, Gemini 2.5 thinking mode, etc.).
                # The openai SDK preserves it as an extra field.
                thinking = getattr(choice.message, "reasoning_content", None)
                if thinking:
                    llm_event["thinking"] = thinking
                if choice.message.tool_calls:
                    llm_event["tool_calls"] = [
                        {"name": tc.function.name, "arguments": tc.function.arguments}
                        for tc in choice.message.tool_calls
                    ]
                if response.usage:
                    usage_dict: dict = {
                        "prompt_tokens": response.usage.prompt_tokens or 0,
                        "completion_tokens": response.usage.completion_tokens or 0,
                    }
                    ptd = response.usage.prompt_tokens_details
                    if ptd and getattr(ptd, "cached_tokens", None):
                        usage_dict["cached_tokens"] = ptd.cached_tokens
                        total_prompt = response.usage.prompt_tokens or 0
                        if ptd.cached_tokens > total_prompt > 0:
                            logger.warning(
                                "[pilot] cached_tokens (%d) > prompt_tokens (%d) — upstream API bug",
                                ptd.cached_tokens,
                                total_prompt,
                            )
                        elif total_prompt > 0:
                            hit_pct = ptd.cached_tokens / total_prompt * 100
                            logger.debug("[pilot] Cache: %d/%d (%.0f%%)", ptd.cached_tokens, total_prompt, hit_pct)
                    ctd = response.usage.completion_tokens_details
                    if ctd and getattr(ctd, "reasoning_tokens", None):
                        usage_dict["reasoning_tokens"] = ctd.reasoning_tokens
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
                # LLM stopped calling tools — always counts as stalling
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
                    # First empty response: retry immediately without counting
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

        except ToolExecutionError:
            raise

        except OpenAIError as e:
            state.consecutive_timeouts = 0
            error_str = str(e)
            logger.warning("[pilot] LLM error: %s", e)
            if game_log:
                game_log.emit("llm_error", error_type=type(e).__name__, error_message=error_str[:500])

            # Permanent failures - abort immediately to avoid wasting
            # API tokens on the other player(s).
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

            # Transient error - keep actions flowing while waiting to retry
            try:
                await execute_tool(session, "pass_priority", {})
            except ToolExecutionError:
                await asyncio.sleep(5)

            _reset_context(
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

    # Initialize OpenAI-compatible client
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
        personality="sleepwalker",
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
                # Fail fast if toolset references tools the MCP bridge doesn't have
                if tools is not None:
                    available_mcp_names = {t.name for t in tools_result.tools}
                    unknown = tools - available_mcp_names
                    if unknown:
                        raise ValueError(
                            f"Toolset references unknown MCP tools: {sorted(unknown)}. "
                            f"Available: {sorted(available_mcp_names)}"
                        )
                openai_tools = mcp_tools_to_openai(tools_result.tools, tools)
                tool_names = [t["function"]["name"] for t in openai_tools]
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

    # Determine project root
    if args.project_root:
        project_root = args.project_root.resolve()
    else:
        project_root = Path.cwd().resolve()
        if project_root.name == "puppeteer" and project_root.parent.name == "src":
            project_root = project_root.parent.parent.parent
        elif project_root.name == "puppeteer":
            project_root = project_root.parent

    provider = args.provider or DEFAULT_LLM_PROVIDER

    # API key: CLI arg > provider-specific env var based on provider.
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

    # Load system prompt: CLI arg > prompts.json default
    system_prompt = args.system_prompt or _load_default_system_prompt()

    # Parse tool names: CLI arg > default
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
    except PermanentLLMError as e:
        logger.error("[pilot] Permanent LLM failure: %s", e)
        return PERMANENT_FAILURE_EXIT_CODE

    return 0


if __name__ == "__main__":
    sys.exit(main())
