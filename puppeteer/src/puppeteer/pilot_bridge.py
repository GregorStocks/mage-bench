"""Bridge-facing helpers used by the pilot loop."""

import json
from collections.abc import Callable, Sequence
from logging import Logger
from pathlib import Path

from mcp import ClientSession
from mcp.types import Tool

from puppeteer.game_log import GameLogWriter
from puppeteer.pilot_game_state import _parse_context_metadata
from puppeteer.tool_error import ToolExecutionError, extract_text_content
from schemas.game_export_types import (
    Choice,
    Decision,
    MultiAmountItem,
    PilotContext,
    Snapshot,
    require_snapshot,
)


def _build_pilot_snapshot(data: dict, board: list[dict] | None, decision: Decision) -> Snapshot:
    """Build a typed snapshot from a pass_priority/get_action_choices result."""
    players: list[dict] = []
    active_player: str | None = None
    if board:
        for p in board:
            name = p.get("name")
            life = p.get("life")
            library_size = p.get("library_size", 0)
            assert isinstance(name, str) and name, f"pilot board player missing name: {p!r}"
            assert isinstance(life, int), f"pilot board player life must be an int, got {life!r}"
            assert isinstance(library_size, int), (
                f"pilot board player library_size must be an int, got {library_size!r}"
            )
            if p.get("is_active"):
                active_player = name
            hand = p.get("hand")
            battlefield = p.get("battlefield")
            graveyard = p.get("graveyard")
            player: dict = {
                "name": name,
                "life": life,
                "library_size": library_size,
                "battlefield": [] if battlefield is None else battlefield,
                "graveyard": [] if graveyard is None else graveyard,
                "hand": [] if hand is None else hand,
            }
            player["hand_count"] = p.get("hand_size", len(hand) if hand is not None else 0)
            for zone in ("battlefield", "graveyard", "exile", "commanders"):
                if p.get(zone):
                    player[zone] = p[zone]
            if p.get("counters"):
                player["counters"] = p["counters"]
            players.append(player)

    _, _, step, context_active_player = _parse_context_metadata(data.get("context"))
    raw_seq = data.get("game_seq", data.get("board_cursor", 0))
    assert isinstance(raw_seq, int), f"pilot snapshot missing integer game_seq/board_cursor: {data!r}"
    stack = data.get("stack")
    snapshot_payload: dict[str, object] = {
        "seq": raw_seq,
        "turn": decision.turn,
        "phase": decision.phase,
        "step": step,
        "active_player": active_player or context_active_player,
        "priority_player": decision.player,
        "players": players,
        "stack": [] if stack is None else stack,
    }
    if data.get("combat") is not None:
        snapshot_payload["combat"] = data["combat"]
    return require_snapshot(snapshot_payload, source="pilot snapshot")


def _build_pilot_decision(data: dict) -> Decision:
    """Build a decision-like dict from a pass_priority/get_action_choices result."""
    raw_choices = data.get("choices")
    if raw_choices is None:
        raw_choices = []
    choices = Choice.coerce_list(raw_choices)
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
        snapshot_index=0,
        player="You",
        turn=0,
        phase="",
        action_type="" if action_type is None else action_type,
        response_type="" if response_type is None else response_type,
        message="" if message is None else message,
        choices=choices,
        choice_count=len(choices),
        is_forced=len(choices) <= 1,
        llm_event_indices=[],
        subsequent_actions=[],
    )

    context_turn, context_phase, _, _ = _parse_context_metadata(data.get("context"))
    if context_turn is not None:
        decision.turn = context_turn
    if context_phase is not None:
        decision.phase = context_phase

    board = data.get("board")
    if isinstance(board, list):
        for p in board:
            if isinstance(p, dict) and p.get("is_you"):
                decision.player = p["name"]
                break

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
        decision.pilot_context = PilotContext.from_mapping(pilot_ctx)

    raw_items = data.get("items")
    if raw_items:
        decision.items = MultiAmountItem.coerce_list(raw_items)
        if "total_min" in data:
            decision.total_min = data["total_min"]
        if "total_max" in data:
            decision.total_max = data["total_max"]

    return decision


def mcp_tools_to_openai(mcp_tools: Sequence[Tool], allowed_tools: set[str] | None = None) -> list[dict]:
    """Convert MCP tool definitions to OpenAI function calling format."""
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


def _tool_execution_error_result(error: ToolExecutionError, game_seq: int | None) -> str:
    """Build a structured tool_call payload for fatal MCP execution failures."""
    result: dict[str, object] = {
        "success": False,
        "error": str(error),
        "error_code": "tool_execution_error",
        "retryable": False,
    }
    if game_seq is not None:
        result["game_seq"] = game_seq
    return json.dumps(result, separators=(",", ":"))


def _record_tool_execution_failure(
    error: ToolExecutionError,
    username: str,
    game_dir: Path | None,
    game_log: GameLogWriter | None,
    *,
    logger: Logger,
    log_error_fn: Callable[[Logger, Path | None, str, str], None],
) -> None:
    """Persist fatal MCP tool failures so exports don't look falsely clean."""
    error_str = str(error)
    if game_log:
        game_log.emit("llm_error", error_type=type(error).__name__, error_message=error_str[:500])
    log_error_fn(logger, game_dir, username, f"[pilot] Fatal tool error: {error_str}")
