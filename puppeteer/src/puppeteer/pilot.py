"""Pilot: LLM-powered game player that makes strategic decisions via MCP tools."""

import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import ExitStack
from pathlib import Path

from mcp import ClientSession
from openai import AsyncOpenAI

from puppeteer.auto_pass import auto_pass_loop
from puppeteer.config import load_prompts
from puppeteer.decision_renderer import BASIC_LAND_NAMES, render_decision
from puppeteer.game_log import GameLogWriter
from puppeteer.llm_cost import (
    DEFAULT_BASE_URL,
    get_model_price,
    load_prices,
    required_api_key_env,
    write_cost_file,
)
from puppeteer.log import get_logger, log_error, setup_logging

logger = get_logger(__name__)

DEFAULT_MODEL = "google/gemini-2.0-flash-001"

# Exit code returned when the LLM permanently fails (404 model not found,
# 402/403 credits exhausted).  The orchestrator checks for this to abort the
# game early instead of wasting API tokens on the other player.
PERMANENT_FAILURE_EXIT_CODE = 3


class PermanentLLMFailure(Exception):
    """Raised when the LLM is permanently unreachable (model not found, credits exhausted)."""


MAX_TOKENS = 20_000
LLM_REQUEST_TIMEOUT_SECS = 120
MAX_CONSECUTIVE_TIMEOUTS = 3
MAX_GAME_DURATION_SECS = 3 * 3600  # 3 hours absolute maximum

# Context window management.
# History is append-only; before each LLM call we render a bounded context
# window from history: recent messages at full fidelity, older messages
# with tool results summarised to save tokens.
CONTEXT_RECENT_COUNT = 40  # recent history entries kept at full fidelity
CONTEXT_SUMMARY_COUNT = 20  # older entries included as compact summaries
TOOL_RESULT_MAX_CHARS = 200  # max chars for a summarised tool result
RENDER_INTERVAL = 5  # re-render context every N iterations when history is long


def _extract_oracle_texts_from_board(board: list[dict]) -> dict[str, dict]:
    """Extract oracle text from board payload's rules fields.

    The bridge includes `rules` on every card (hand, battlefield, etc.).
    Convert these to the oracle_texts format expected by render_decision().
    """
    oracle_texts: dict[str, dict] = {}
    for player in board:
        for zone in ("hand", "battlefield", "graveyard", "exile", "commanders"):
            for card in player.get(zone, []):
                if not isinstance(card, dict):
                    continue
                name = card.get("name", "")
                if not name or name in BASIC_LAND_NAMES or name in oracle_texts:
                    continue
                rules = card.get("rules", [])
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
            player["hand_count"] = p.get("hand_size", len(p.get("hand", [])))
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


def _build_pilot_decision(data: dict) -> dict:
    """Build a decision-like dict from a pass_priority/get_action_choices result.

    Extracts the fields that render_decision() reads from a decision.
    """
    decision: dict = {
        "index": 0,
        "snapshotIndex": 0,
        "player": "You",
        "turn": 0,
        "phase": "",
        "actionType": data.get("action_type", ""),
        "responseType": data.get("response_type", ""),
        "message": data.get("message", ""),
        "choices": data.get("choices", []),
        "choiceCount": len(data.get("choices", [])),
        "isForced": len(data.get("choices", [])) <= 1,
    }

    # Parse context string for turn/phase: "T3 Precombat Main/Precombat Main (Alice) YOUR_MAIN"
    context = data.get("context", "")
    if context:
        import re

        m = re.match(r"T(\d+)\s+(.+?)(?:\s+\(|$)", context)
        if m:
            decision["turn"] = int(m.group(1))
            decision["phase"] = m.group(2).split("/")[0].strip().upper().replace(" ", "_")

    # Find player name from board
    board = data.get("board", [])
    if isinstance(board, list):
        for p in board:
            if isinstance(p, dict) and p.get("is_you"):
                decision["player"] = p["name"]
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
        decision["pilotContext"] = pilot_ctx

    return decision


def _render_for_pilot(result_text: str, last_board: list[dict] | None) -> tuple[str, list[dict] | None]:
    """Render a pass_priority/get_action_choices result for LLM consumption.

    Returns (rendered_text, updated_board). The board is tracked so that
    board_unchanged results can use the last-known board.
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

    # Extract oracle texts from board's rules fields
    oracle_texts = _extract_oracle_texts_from_board(board) if board else {}

    deciding_player = decision["player"]

    rendered = render_decision(
        decision,
        snapshot,
        oracle_texts=oracle_texts,
        deciding_player=deciding_player,
        include_card_reference=True,
    )

    # Append operational metadata the LLM needs to respond
    lines = [rendered]
    resp_type = data.get("response_type", "")
    respond_with = data.get("respond_with", "")
    if respond_with:
        lines.append(f"  Respond: {respond_with}")
    elif resp_type:
        lines.append(f"  Response type: {resp_type}")

    mana_pool = data.get("mana_pool")
    if mana_pool and any(v > 0 for v in mana_pool.values()):
        pool_str = ", ".join(f"{k}={v}" for k, v in mana_pool.items() if v > 0)
        lines.append(f"  Mana pool: {pool_str}")

    return "\n".join(lines), last_board


def _summarize_tool_result(tool_name: str, content: str) -> str:
    """Compress a tool result to a short summary for older context entries.

    Parses the JSON result and extracts key fields per tool type.
    Falls back to truncation for unknown tools or invalid JSON.
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content[:TOOL_RESULT_MAX_CHARS]

    if tool_name == "pass_priority":
        if data.get("player_dead"):
            return "player_dead"
        if data.get("action_pending"):
            stop = data.get("stop_reason", "")
            action_type = data.get("action_type", "?")
            parts = []
            if stop:
                parts.append(f"action_pending({action_type}, {stop})")
            else:
                parts.append(f"action_pending({action_type})")
            # Include choice summary (pass_priority now returns choices inline)
            resp_type = data.get("response_type", "")
            if resp_type:
                parts.append(resp_type)
            choices = data.get("choices", [])
            if choices:
                names = [c.get("name", c.get("description", "?"))[:30] for c in choices[:3]]
                parts.append(f"{len(choices)} choices: {', '.join(names)}")
            msg = data.get("message", "")
            if msg and not choices:
                parts.append(msg[:60])
            return "; ".join(parts)
        stop = data.get("stop_reason", "")
        if stop:
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
        resp_type = data.get("response_type", "")
        if resp_type:
            parts.append(resp_type)
        choices = data.get("choices", [])
        if choices:
            names = [c.get("name", c.get("description", "?"))[:30] for c in choices[:3]]
            parts.append(f"{len(choices)} choices: {', '.join(names)}")
        msg = data.get("message", "")
        if msg and not choices:
            parts.append(msg[:60])
        return "; ".join(parts)

    if tool_name == "get_game_state":
        parts = []
        if "turn" in data:
            parts.append(f"T{data['turn']}")
        if "phase" in data:
            parts.append(data["phase"])
        for p in data.get("players", []):
            name = p.get("name", "?")
            life = p.get("life", "?")
            bf = len(p.get("battlefield", []))
            parts.append(f"{name}:{life}hp/{bf}perm")
        return "; ".join(parts) if parts else content[:TOOL_RESULT_MAX_CHARS]

    if tool_name == "get_game_log":
        total = data.get("total_length", "?")
        truncated = data.get("truncated", False)
        since = data.get("since_turn")
        log_text = data.get("log", "")
        prefix = f"log({total} chars"
        if since is not None:
            prefix += f", since_turn={since}"
        if truncated:
            prefix += ", truncated"
        prefix += "): "
        remaining = TOOL_RESULT_MAX_CHARS - len(prefix)
        if remaining > 0 and log_text:
            return prefix + log_text[:remaining]
        return prefix.rstrip(": ")

    # get_oracle_text, send_chat_message, unknown
    return content[:TOOL_RESULT_MAX_CHARS]


def _find_tool_name(history: list[dict], tool_result_idx: int, tool_call_id: str) -> str:
    """Find the tool name for a tool result by searching backward for its assistant message."""
    for j in range(tool_result_idx - 1, -1, -1):
        msg = history[j]
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []):
                if tc.get("id") == tool_call_id:
                    return tc.get("function", {}).get("name", "")
            break
    return ""


def _extract_last_reasoning(history: list[dict]) -> str:
    """Extract the last assistant reasoning text from history (for context resets)."""
    for msg in reversed(history):
        if msg.get("role") == "assistant" and msg.get("content"):
            return msg["content"][:300]
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
    role = msg.get("role", "")
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
        if msg.get("role") == "tool" and len(msg.get("content", "")) > TOOL_RESULT_MAX_CHARS:
            tool_name = _find_tool_name(history, i, msg.get("tool_call_id", ""))
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
        "Play cards with id=pN, pass with answer=false."
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
    try:
        state_result = await execute_tool(session, "get_game_state", {})
        state_data = json.loads(state_result)
        if state_data.get("error"):
            return ""
        parts: list[str] = []
        if "turn" in state_data:
            parts.append(f"Turn {state_data['turn']}")
        if "phase" in state_data:
            parts.append(state_data["phase"])
        for p in state_data.get("players", []):
            name = p.get("name", "?")
            life = p.get("life", "?")
            bf = len(p.get("battlefield", []))
            hand = p.get("hand_count", p.get("hand_size", "?"))
            parts.append(f"{name}: {life}hp, {bf} permanents, {hand} cards")
        return "Current game state: " + "; ".join(parts) + ". "
    except Exception:
        return ""


# Tools that are purely informational (don't advance game state).
# Used by stall detection to classify LLM turns.
INFO_ONLY_TOOLS = {"get_game_state", "get_oracle_text", "send_chat_message"}


def _load_default_system_prompt() -> str:
    """Load the default system prompt from prompts.json."""
    prompts = load_prompts(None)
    assert "default" in prompts, "prompts.json must contain a 'default' key"
    return prompts["default"]


def mcp_tools_to_openai(mcp_tools, allowed_tools: set[str] | None = None) -> list[dict]:
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
        return result.content[0].text
    except Exception as e:
        return json.dumps({"error": str(e)})


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


def build_initial_message(pass_priority_result: dict) -> str:
    """Build the initial user message from a pass_priority result.

    Used both by the real pilot loop (via _prefetch_first_action) and by
    golden prompt tests.
    """
    if pass_priority_result.get("game_over"):
        return "The game is over."
    if not pass_priority_result.get("action_pending"):
        return "The game is starting. Call pass_priority to get your first decision."

    action_type = pass_priority_result.get("action_type", "")
    message = pass_priority_result.get("message", "")

    if "Mulligan" in message or "mulligan" in message.lower():
        return (
            f"The game is starting. Your first decision: {message}\n"
            f"Call get_action_choices to see your hand, then choose_action to decide."
        )
    elif action_type:
        return (
            f"The game is starting. Your first decision ({action_type}): {message}\n"
            f"Call get_action_choices to see your options, then choose_action to decide."
        )
    else:
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
    cache_control: dict | None = None,
) -> None:
    """Run the LLM-driven game-playing loop."""
    # Pre-fetch the first decision so the LLM knows what it's deciding
    # instead of blindly calling pass_priority with confusing yield params.
    initial_message = await _prefetch_first_action(session)
    history: list[dict] = [
        {"role": "user", "content": initial_message},
    ]
    state_summary = ""
    model_price = get_model_price(model, prices)
    cumulative_cost = 0.0
    empty_responses = 0  # consecutive LLM responses with no reasoning text
    last_was_empty = False  # retry once on first empty response before counting
    consecutive_timeouts = 0
    turns_without_progress = 0  # LLM turns without a successful game action
    MAX_TURNS_WITHOUT_PROGRESS = 20
    consecutive_pass_errors = 0  # consecutive identical pass_priority errors
    last_pass_error_msg = ""
    MAX_CONSECUTIVE_PASS_ERRORS = 3
    consecutive_truncations = 0  # consecutive finish_reason=length (model can't fit tool call)
    MAX_CONSECUTIVE_TRUNCATIONS = 3
    consecutive_empty_errors = 0  # consecutive tool calls returning {"error": ""}
    MAX_CONSECUTIVE_EMPTY_ERRORS = 10  # bridge is dead if every tool returns empty error
    last_game_seq: int | None = None  # game-level seq from most recent tool result
    board_tracker = BoardCursorTracker()
    last_board: list[dict] | None = None  # last-known board for rendering when board_unchanged
    game_start = time.monotonic()
    # Chat nudge tracking: encourage LLMs to chat regularly
    current_game_turn: int = 0  # parsed from pass_priority context "T3 ..."
    last_chat_turn: int = 0  # game turn when LLM last sent a chat message
    # Render caching: reuse rendered prefix between full re-renders to improve
    # prompt cache hit rates.  Only re-render every RENDER_INTERVAL iterations.
    cached_render: list[dict] | None = None
    cached_history_len: int = 0
    render_counter: int = 0

    while True:
        if time.monotonic() - game_start > MAX_GAME_DURATION_SECS:
            logger.warning("[pilot] Maximum game duration exceeded, switching to auto-pass")
            if game_log:
                game_log.emit("auto_pilot_mode", reason="max_duration_exceeded")
            await auto_pass_loop(session, game_dir, username, "pilot")
            return
        try:
            # Render context from history; use cached prefix when possible
            if len(history) > CONTEXT_RECENT_COUNT:
                render_counter += 1
                need_rerender = cached_render is None or render_counter % RENDER_INTERVAL == 0
                if need_rerender:
                    state_summary = await _fetch_state_summary(session)
                    messages = _render_context(history, system_prompt, state_summary, cache_control)
                    cached_render = list(messages)
                    cached_history_len = len(history)
                    render_counter = 0
                else:
                    messages = list(cached_render) + history[cached_history_len:]
            else:
                messages = _render_context(history, system_prompt, state_summary, cache_control)
                cached_render = None
                render_counter = 0

            # Tail cache breakpoint: mark the boundary between the stable
            # cached_render prefix and the dynamic tail so Anthropic can cache
            # the entire prefix (~50-80k tokens).  Uses a copy to avoid
            # mutating the cached_render template.
            if cache_control and cached_render is not None and len(cached_render) > 0:
                tail_idx = len(cached_render) - 1
                marked = _with_cache_control(messages[tail_idx], cache_control)
                if marked is not messages[tail_idx]:
                    messages[tail_idx] = marked

            create_kwargs: dict = dict(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                max_tokens=MAX_TOKENS,
            )
            extra_body: dict = {}
            if reasoning_effort:
                extra_body["reasoning"] = {"effort": reasoning_effort}
            if ignore_providers:
                extra_body["provider"] = {"ignore": ignore_providers}
            if extra_body:
                create_kwargs["extra_body"] = extra_body
            response = await asyncio.wait_for(
                client.chat.completions.create(**create_kwargs),
                timeout=LLM_REQUEST_TIMEOUT_SECS,
            )
            consecutive_timeouts = 0
            if not response.choices:
                logger.warning("[pilot] LLM returned empty/null choices, retrying...")
                continue
            choice = response.choices[0]

            # Warn loudly if the response was truncated due to max_tokens.
            # This means the model ran out of output budget (often reasoning
            # models burning tokens on thinking) and couldn't emit a tool call.
            if choice.finish_reason == "length":
                consecutive_truncations += 1
                tokens_used = (response.usage.completion_tokens or 0) if response.usage else "?"
                logger.warning(
                    "[pilot] OUTPUT TRUNCATED: finish_reason=length, completion_tokens=%s/%s. "
                    "Model hit max_tokens cap before producing a tool call. [%d]",
                    tokens_used,
                    MAX_TOKENS,
                    consecutive_truncations,
                )
                if consecutive_truncations >= MAX_CONSECUTIVE_TRUNCATIONS:
                    logger.warning("[pilot] Repeated truncations, resetting conversation context")
                    if game_log:
                        game_log.emit("context_reset", reason="repeated_truncations")
                    last_reasoning = _extract_last_reasoning(history)
                    history = [
                        {
                            "role": "user",
                            "content": _build_reset_message(
                                "Continue playing. Be concise. Call pass_priority.",
                                last_reasoning,
                            ),
                        },
                    ]
                    state_summary = ""
                    cached_render = None
                    cached_history_len = 0
                    render_counter = 0
                    consecutive_truncations = 0
                    board_tracker.reset()
                    last_board = None
                    continue
            else:
                consecutive_truncations = 0

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
                cumulative_cost += call_cost
                if game_dir:
                    write_cost_file(game_dir, username, cumulative_cost)

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
                llm_event["cumulative_cost_usd"] = round(cumulative_cost, 6)
                if last_game_seq is not None:
                    llm_event["game_seq"] = last_game_seq
                game_log.emit("llm_response", **llm_event)

            # If the LLM produced tool calls, process them
            if choice.message.tool_calls:
                # Per-turn tracking for stall detection
                turn_had_successful_action = False
                turn_had_actionable_opportunity = False
                turn_tools_called = set()

                # Tool calls present = LLM is functioning, reset degradation counter.
                # Gemini often omits reasoning text for obvious actions (like passing) -
                # that's normal, not degradation.
                if choice.message.content:
                    logger.info("[pilot] Thinking: %s", choice.message.content)
                empty_responses = 0
                last_was_empty = False
                # Build a clean assistant message dict for cross-provider
                # compatibility.  The raw ChatCompletionMessage includes extra
                # fields (refusal, annotations, audio, function_call) that
                # some providers (notably xAI/Grok) reject with 422 errors.
                assistant_msg: dict = {"role": "assistant", "content": choice.message.content}
                if choice.message.tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in choice.message.tool_calls
                    ]
                history.append(assistant_msg)

                for tool_call in choice.message.tool_calls:
                    fn = tool_call.function
                    args = json.loads(fn.arguments) if fn.arguments else {}

                    board_tracker.inject(fn.name, args)
                    logger.info("[pilot] Tool: %s(%s)", fn.name, json.dumps(args, separators=(",", ":")))

                    tool_start = time.monotonic()
                    result_text = await execute_tool(session, fn.name, args)
                    tool_latency_ms = int((time.monotonic() - tool_start) * 1000)

                    # Extract game_seq from tool result for event ordering
                    try:
                        _result_data = json.loads(result_text)
                        if isinstance(_result_data, dict) and "game_seq" in _result_data:
                            last_game_seq = _result_data["game_seq"]
                    except (json.JSONDecodeError, TypeError):
                        pass
                    board_tracker.extract(result_text)

                    # Log tool call to JSONL
                    if game_log:
                        game_log.emit(
                            "tool_call",
                            call_id=tool_call.id,
                            tool=fn.name,
                            arguments=args,
                            result=result_text,
                            latency_ms=tool_latency_ms,
                            game_seq=last_game_seq,
                        )

                    # Detect dead bridge: all tools return {"error": ""}
                    if result_text == '{"error": ""}':
                        consecutive_empty_errors += 1
                        if consecutive_empty_errors >= MAX_CONSECUTIVE_EMPTY_ERRORS:
                            log_error(
                                logger,
                                game_dir,
                                username,
                                f"[pilot] {consecutive_empty_errors} consecutive empty errors "
                                f"— bridge is dead, exiting",
                            )
                            if game_log:
                                game_log.emit(
                                    "auto_pilot_mode",
                                    reason="bridge_dead",
                                    consecutive_empty_errors=consecutive_empty_errors,
                                )
                            return
                    else:
                        consecutive_empty_errors = 0

                    # Log interesting results and track for stall detection
                    turn_tools_called.add(fn.name)
                    if fn.name == "choose_action":
                        result_data = json.loads(result_text)
                        action_taken = result_data.get("action_taken", "")
                        success = result_data.get("success", False)
                        if success:
                            logger.info("[pilot] Action: %s", action_taken)
                            turn_had_successful_action = True
                            turns_without_progress = 0
                        else:
                            logger.warning("[pilot] Action failed: %s", result_data.get("error", ""))
                            turn_had_actionable_opportunity = True
                    elif fn.name == "get_action_choices":
                        result_data = json.loads(result_text)
                        action_type = result_data.get("action_type", "")
                        msg = result_data.get("message", "")
                        choices = result_data.get("choices", [])
                        if result_data.get("error"):
                            turn_had_actionable_opportunity = True
                        elif choices:
                            logger.info("[pilot] Choices for %s: %d options", action_type, len(choices))
                            turn_had_actionable_opportunity = True
                        else:
                            logger.info("[pilot] Action: %s - %s", action_type, msg[:100])
                    elif fn.name == "pass_priority":
                        try:
                            result_data = json.loads(result_text)
                            # Parse game turn from context for chat nudge tracking
                            _ctx = result_data.get("context", "")
                            if _ctx.startswith("T"):
                                try:
                                    current_game_turn = int(_ctx[1:].split()[0])
                                except (ValueError, IndexError):
                                    pass
                            if result_data.get("action_pending"):
                                turn_had_actionable_opportunity = True
                                consecutive_pass_errors = 0
                                last_pass_error_msg = ""
                            if result_data.get("error"):
                                turn_had_actionable_opportunity = True
                                err_msg = result_data["error"]
                                if err_msg == last_pass_error_msg:
                                    consecutive_pass_errors += 1
                                else:
                                    consecutive_pass_errors = 1
                                    last_pass_error_msg = err_msg
                                if consecutive_pass_errors >= MAX_CONSECUTIVE_PASS_ERRORS:
                                    logger.warning(
                                        "[pilot] %d consecutive identical pass_priority errors, forcing plain pass",
                                        consecutive_pass_errors,
                                    )
                                    if game_log:
                                        game_log.emit(
                                            "forced_pass",
                                            reason="repeated_pass_error",
                                            error=err_msg,
                                            count=consecutive_pass_errors,
                                        )
                                    result_text = await execute_tool(session, "pass_priority", {})
                                    consecutive_pass_errors = 0
                                    last_pass_error_msg = ""
                            else:
                                consecutive_pass_errors = 0
                                last_pass_error_msg = ""
                        except (json.JSONDecodeError, TypeError):
                            pass

                    # Track chat sends for nudge system
                    if fn.name == "send_chat_message":
                        last_chat_turn = current_game_turn

                    # Detect game_over/player_dead from ANY tool result
                    try:
                        _rd = json.loads(result_text)
                        if isinstance(_rd, dict):
                            if _rd.get("game_over"):
                                logger.info("[pilot] Game over detected from %s, switching to auto-pass", fn.name)
                                if game_log:
                                    game_log.emit("auto_pilot_mode", reason="game_over")
                                await auto_pass_loop(session, game_dir, username, "pilot")
                                return
                            if _rd.get("player_dead"):
                                logger.info("[pilot] Player dead detected from %s, switching to auto-pass", fn.name)
                                if game_log:
                                    game_log.emit("auto_pilot_mode", reason="player_dead")
                                await auto_pass_loop(session, game_dir, username, "pilot")
                                return
                    except (json.JSONDecodeError, TypeError):
                        pass

                    # Render pass_priority/get_action_choices for LLM display
                    display_text = result_text
                    if fn.name in ("pass_priority", "get_action_choices"):
                        display_text, last_board = _render_for_pilot(result_text, last_board)
                        # Chat nudge: remind LLM to chat if it's been silent too long
                        turns_since_chat = current_game_turn - last_chat_turn
                        if turns_since_chat >= 2 and display_text != result_text:
                            display_text += (
                                f"\n\n[It's been {turns_since_chat} turns since you last "
                                f"chatted — send a message to your opponent!]"
                            )

                    history.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": display_text,
                        }
                    )

                # Stall counter: only count turns where LLM had a real chance to act
                if not turn_had_successful_action:
                    if turn_had_actionable_opportunity or not turn_tools_called or turn_tools_called <= INFO_ONLY_TOOLS:
                        turns_without_progress += 1
                    # else: passive wait (pass_priority timeout) — don't penalize
            else:
                # LLM stopped calling tools — always counts as stalling
                turns_without_progress += 1
                content = choice.message.content
                if content:
                    content = content.strip()
                if content:
                    logger.info("[pilot] Thinking: %s", content[:500])
                    history.append({"role": "assistant", "content": content})
                    empty_responses = 0
                    last_was_empty = False
                elif not last_was_empty:
                    # First empty response: retry immediately without counting
                    logger.warning("[pilot] Empty response from LLM, retrying...")
                    last_was_empty = True
                    continue
                else:
                    last_was_empty = False
                    empty_responses += 1
                    logger.warning("[pilot] Empty response from LLM (no tools, no text) [%d]", empty_responses)
                    if empty_responses >= 10:
                        logger.warning("[pilot] LLM appears degraded (no tools or text), switching to auto-pass mode")
                        if game_log:
                            game_log.emit("auto_pilot_mode", reason="LLM degraded (10+ empty responses)")
                        try:
                            await execute_tool(
                                session,
                                "send_chat_message",
                                {"message": "My brain is fried... going on autopilot for the rest of this game. GG!"},
                            )
                        except Exception:
                            pass
                        await auto_pass_loop(session, game_dir, username, "pilot")
                        return
                history.append(
                    {
                        "role": "user",
                        "content": "Continue playing. Call pass_priority.",
                    }
                )

            # If the LLM is spinning without advancing game state, auto-pass
            # until something interesting happens (new turn, new cards, etc.)
            if turns_without_progress >= MAX_TURNS_WITHOUT_PROGRESS:
                last_tools = sorted(turn_tools_called) if choice.message.tool_calls and turn_tools_called else []
                logger.warning(
                    "[pilot] Stalled: %d turns without progress, last tools: %s, auto-passing until next event",
                    turns_without_progress,
                    last_tools or "none",
                )
                if game_log:
                    game_log.emit(
                        "stall",
                        turns_without_progress=turns_without_progress,
                        last_tools=last_tools,
                    )
                try:
                    await execute_tool(
                        session,
                        "send_chat_message",
                        {"message": "Brain freeze! Auto-passing until next turn..."},
                    )
                except Exception:
                    pass
                try:
                    await execute_tool(session, "pass_priority", {})
                    logger.info("[pilot] Auto-passed stalled action")
                except Exception as e:
                    logger.warning("[pilot] Auto-pass failed: %s", e)
                turns_without_progress = 0
                # Reset conversation so the LLM gets a fresh start
                last_reasoning = _extract_last_reasoning(history)
                history = [
                    {
                        "role": "user",
                        "content": _build_reset_message(
                            "A new turn has started. Call pass_priority to continue.",
                            last_reasoning,
                        ),
                    },
                ]
                state_summary = ""
                cached_render = None
                cached_history_len = 0
                render_counter = 0
                continue

        except asyncio.TimeoutError:
            consecutive_timeouts += 1
            logger.warning(
                "[pilot] LLM request timed out after %ss [%d]",
                LLM_REQUEST_TIMEOUT_SECS,
                consecutive_timeouts,
            )
            if game_log:
                game_log.emit(
                    "llm_error",
                    error_type="timeout",
                    error_message=f"Timed out after {LLM_REQUEST_TIMEOUT_SECS}s [{consecutive_timeouts}]",
                )
            try:
                await execute_tool(session, "pass_priority", {})
            except Exception:
                await asyncio.sleep(5)

            if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                logger.warning("[pilot] Repeated LLM timeouts, resetting conversation context")
                if game_log:
                    game_log.emit("context_reset", reason="repeated_timeouts")
                last_reasoning = _extract_last_reasoning(history)
                history = [
                    {
                        "role": "user",
                        "content": _build_reset_message(
                            "Continue playing. Call pass_priority.",
                            last_reasoning,
                        ),
                    },
                ]
                state_summary = ""
                cached_render = None
                cached_history_len = 0
                render_counter = 0
                consecutive_timeouts = 0
                board_tracker.reset()
                last_board = None

        except Exception as e:
            consecutive_timeouts = 0
            error_str = str(e)
            logger.warning("[pilot] LLM error: %s", e)
            if game_log:
                game_log.emit("llm_error", error_type=type(e).__name__, error_message=error_str[:500])

            # Permanent failures - abort immediately to avoid wasting
            # API tokens on the other player(s).
            permanent_codes = {"401", "402", "403", "404"}
            if any(code in error_str for code in permanent_codes):
                is_not_found = "404" in error_str and "401" not in error_str
                reason = "Model not found" if is_not_found else "Credits exhausted"
                logger.warning("[pilot] %s, aborting", reason)
                if game_log:
                    game_log.emit("permanent_llm_failure", reason=reason)
                try:
                    await execute_tool(
                        session,
                        "send_chat_message",
                        {"message": f"{reason}... aborting game. GG!"},
                    )
                except Exception:
                    pass
                raise PermanentLLMFailure(reason) from None

            # Transient error - keep actions flowing while waiting to retry
            try:
                await execute_tool(session, "pass_priority", {})
            except Exception:
                await asyncio.sleep(5)

            # Reset conversation on error
            last_reasoning = _extract_last_reasoning(history)
            history = [
                {
                    "role": "user",
                    "content": _build_reset_message(
                        "Continue playing. Call pass_priority.",
                        last_reasoning,
                    ),
                },
            ]
            state_summary = ""
            cached_render = None
            cached_history_len = 0
            render_counter = 0


async def run_pilot(
    server: str,
    port: int,
    username: str,
    project_root: Path,
    prices: dict[str, tuple[float, float]],
    deck_path: Path | None = None,
    api_key: str = "",
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    system_prompt: str = "",
    game_dir: Path | None = None,
    max_interactions_per_turn: int | None = None,
    reasoning_effort: str = "",
    tools: set[str] | None = None,
    ignore_providers: list[str] | None = None,
    cache_control: dict | None = None,
) -> None:
    """Run the pilot client."""
    logger.info("[pilot] Starting for %s@%s:%s", username, server, port)
    logger.info("[pilot] Model: %s", model)
    logger.info("[pilot] Base URL: %s", base_url)
    if reasoning_effort:
        logger.info("[pilot] Reasoning effort: %s", reasoning_effort)
    if tools is not None:
        logger.info("[pilot] Custom toolset: %s", sorted(tools))
    if ignore_providers:
        logger.info("[pilot] Ignoring providers: %s", ignore_providers)
    if cache_control:
        logger.debug("[pilot] Prompt cache_control: %s", cache_control)

    # Initialize OpenAI-compatible client
    llm_client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=LLM_REQUEST_TIMEOUT_SECS + 5,
        max_retries=1,
    )

    # Build JVM args for the bridge (same as sleepwalker)
    jvm_args_list = [
        "--add-opens=java.base/java.io=ALL-UNNAMED",
        f"-Dxmage.bridge.server={server}",
        f"-Dxmage.bridge.port={port}",
        "-Dxmage.bridge.personality=sleepwalker",
    ]
    if sys.platform == "darwin":
        jvm_args_list.append("-Dapple.awt.UIElement=true")
    jvm_args = " ".join(jvm_args_list)

    # Pass values that may contain spaces as Maven CLI args (not in MAVEN_OPTS)
    # because MAVEN_OPTS gets shell-split by the mvn script.
    # Maven CLI -D args go through "$@" which preserves spaces correctly.
    mvn_args = ["-q", f"-Dxmage.bridge.username={username}"]
    if deck_path:
        mvn_args.append(f"-Dxmage.bridge.deck={deck_path}")
    if game_dir:
        mvn_args.append(f"-Dxmage.bridge.errorlog={game_dir / f'{username}_errors.log'}")
        mvn_args.append(f"-Dxmage.bridge.bridgelog={game_dir / f'{username}_bridge.jsonl'}")
    if max_interactions_per_turn is not None:
        mvn_args.append(f"-Dxmage.bridge.maxInteractionsPerTurn={max_interactions_per_turn}")
    mvn_args.append("exec:java")

    logger.info("[pilot] Spawning bridge client...")

    game_log = None
    trace_log = None
    with ExitStack() as log_stack:
        if game_dir:
            game_log = log_stack.enter_context(GameLogWriter(game_dir, username))
            trace_log = log_stack.enter_context(GameLogWriter(game_dir, username, suffix="llm_trace"))

        try:
            from puppeteer.bridge_transport import spawn_bridge_http

            async with spawn_bridge_http(
                mvn_args=mvn_args,
                project_root=project_root,
                jvm_args=jvm_args,
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
    parser.add_argument("--api-key", default="", help="API key (prefer OPENROUTER_API_KEY env var)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"LLM model (default: {DEFAULT_MODEL})")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"API base URL (default: {DEFAULT_BASE_URL})")
    parser.add_argument("--system-prompt", default="", help="Custom system prompt")
    parser.add_argument("--game-dir", type=Path, help="Game directory for cost file output")
    parser.add_argument("--max-interactions-per-turn", type=int, help="Loop detection threshold (default 25)")
    parser.add_argument("--reasoning-effort", default="", help="OpenRouter reasoning effort: low, medium, high")
    parser.add_argument("--tools", default="", help="Comma-separated MCP tool names (default: all)")
    parser.add_argument("--ignore-providers", default="", help="Comma-separated OpenRouter providers to exclude")
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

    # API key: CLI arg > provider-specific env var based on base URL.
    required_key_env = required_api_key_env(args.base_url)
    api_key = args.api_key or os.environ.get(required_key_env, "")
    if not api_key.strip():
        logger.error("[pilot] Missing API key for %s", args.base_url)
        logger.error("[pilot] Set %s or pass --api-key.", required_key_env)
        return 2

    prices = load_prices()
    logger.debug("[pilot] Project root: %s", project_root)

    # Load system prompt: CLI arg > prompts.json default
    system_prompt = args.system_prompt or _load_default_system_prompt()

    # Parse tool names: CLI arg > default
    pilot_tools = set(args.tools.split(",")) if args.tools else None
    ignore_providers = args.ignore_providers.split(",") if args.ignore_providers else None
    cache_control = json.loads(args.cache_control) if args.cache_control else None

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
                base_url=args.base_url,
                system_prompt=system_prompt,
                game_dir=args.game_dir,
                prices=prices,
                max_interactions_per_turn=args.max_interactions_per_turn,
                reasoning_effort=args.reasoning_effort,
                tools=pilot_tools,
                ignore_providers=ignore_providers,
                cache_control=cache_control,
            )
        )
    except KeyboardInterrupt:
        pass
    except PermanentLLMFailure as e:
        logger.error("[pilot] Permanent LLM failure: %s", e)
        return PERMANENT_FAILURE_EXIT_CODE

    return 0


if __name__ == "__main__":
    sys.exit(main())
