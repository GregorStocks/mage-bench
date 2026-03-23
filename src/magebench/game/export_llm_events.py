"""LLM event parsing helpers for game export construction."""

import json
from datetime import datetime
from pathlib import Path

_LLM_EVENT_TYPES = {
    "game_start",
    "llm_response",
    "tool_call",
    "stall",
    "context_reset",
    "llm_error",
    "auto_pilot_mode",
}


def compute_thinking_time(llm_events: list[dict]) -> dict[str, float]:
    """Compute per-player thinking time from sorted LLM events.

    For each consecutive pair of events, the time gap is attributed to the
    player of the earlier event. This approximates wall-clock time each player
    spent with priority (similar to a chess clock).

    Returns {player_name: total_seconds}.
    """
    thinking: dict[str, float] = {}
    for i in range(len(llm_events) - 1):
        player = llm_events[i].get("player")
        if not player:
            continue
        ts_a = llm_events[i].get("ts")
        ts_b = llm_events[i + 1].get("ts")
        if not ts_a or not ts_b:
            continue
        try:
            dt_a = datetime.fromisoformat(ts_a)
            dt_b = datetime.fromisoformat(ts_b)
        except ValueError:
            continue
        gap = (dt_b - dt_a).total_seconds()
        if gap > 0:
            thinking[player] = thinking.get(player, 0.0) + gap
    return thinking


def compute_tool_call_counts(llm_events: list[dict]) -> dict[str, tuple[int, int]]:
    """Compute successful/failed tool call counts from exported llm_events."""
    player_tool_calls: dict[str, tuple[int, int]] = {}
    for event in llm_events:
        if event.get("type") != "tool_call":
            continue
        player = event.get("player")
        if not player:
            continue

        ok, failed = player_tool_calls.get(player, (0, 0))
        is_failure = False
        result_str = event.get("result")
        if result_str:
            try:
                result_obj = json.loads(result_str)
                if isinstance(result_obj, dict) and result_obj.get("success") is False:
                    is_failure = True
            except (json.JSONDecodeError, TypeError):
                pass

        if is_failure:
            player_tool_calls[player] = (ok, failed + 1)
        else:
            player_tool_calls[player] = (ok + 1, failed)

    return player_tool_calls


def read_llm_events(
    game_dir: Path,
) -> tuple[
    list[dict],
    dict[str, float],
    dict[str, list[str]],
    dict[str, tuple[int, int]],
    dict[str, float],
]:
    """Read LLM events from all *_llm.jsonl files.

    Returns (llm_events sorted by timestamp, {player_name: total_cost_usd},
    {player_name: available_tools}, {player_name: (ok_count, failed_count)},
    {player_name: thinking_time_secs}).
    """
    events = []
    player_costs: dict[str, float] = {}
    player_tools: dict[str, list[str]] = {}

    for path in sorted(game_dir.glob("*_llm.jsonl")):
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = raw["type"]
            player = raw.get("player")

            if event_type == "game_end" and "total_cost_usd" in raw:
                player_costs[player] = raw["total_cost_usd"]
            elif "cumulative_cost_usd" in raw:
                player_costs[player] = raw["cumulative_cost_usd"]

            if event_type == "game_start" and "available_tools" in raw:
                player_tools[player] = raw["available_tools"]

            if event_type not in _LLM_EVENT_TYPES:
                continue

            exported: dict = {
                "ts": raw.get("ts"),
                "seq": raw.get("seq"),
                "player": player,
                "type": event_type,
            }

            game_seq = raw.get("game_seq")

            if event_type == "game_start":
                model = raw.get("model")
                if model is not None:
                    exported["model"] = model
                available_tools = raw.get("available_tools")
                if available_tools is not None:
                    exported["available_tools"] = available_tools
            elif event_type == "llm_response":
                exported["reasoning"] = raw.get("reasoning")
                if raw.get("thinking"):
                    exported["thinking"] = raw["thinking"]
                if raw.get("tool_calls"):
                    exported["tool_calls"] = raw["tool_calls"]
                usage = raw.get("usage")
                if usage:
                    exported["usage"] = {
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                    }
                    if usage.get("cached_tokens"):
                        exported["usage"]["cached_tokens"] = usage["cached_tokens"]
                    if usage.get("reasoning_tokens"):
                        exported["usage"]["reasoning_tokens"] = usage["reasoning_tokens"]
                if "cost_usd" in raw:
                    exported["cost_usd"] = raw["cost_usd"]
            elif event_type == "tool_call":
                exported["tool"] = raw["tool"]
                assert "arguments" in raw, f"tool_call event missing arguments: {raw!r}"
                arguments = raw["arguments"]
                assert isinstance(arguments, dict), f"tool_call arguments must be an object, got {arguments!r}"
                exported["args"] = arguments
                exported["result"] = raw["result"]
                if "latency_ms" in raw:
                    exported["latency_ms"] = raw["latency_ms"]
                if game_seq is None:
                    result_str = raw["result"]
                    if result_str:
                        try:
                            result_obj = json.loads(result_str)
                            if isinstance(result_obj, dict):
                                game_seq = result_obj.get("game_seq")
                        except (json.JSONDecodeError, TypeError):
                            pass
            elif event_type == "stall":
                exported["turns_without_progress"] = raw.get("turns_without_progress", 0)
                last_tools = raw.get("last_tools")
                if last_tools is not None:
                    exported["last_tools"] = last_tools
            elif event_type == "context_reset":
                exported["reason"] = raw["reason"]
            elif event_type == "llm_error":
                exported["error_type"] = raw["error_type"]
                exported["error_message"] = raw["error_message"]
            elif event_type == "auto_pilot_mode":
                exported["reason"] = raw["reason"]

            if game_seq is not None:
                exported["game_seq"] = game_seq

            events.append(exported)

    events.sort(key=lambda e: e["ts"] if e.get("ts") is not None else "")

    player_tool_calls = compute_tool_call_counts(events)
    player_thinking = compute_thinking_time(events)

    return events, player_costs, player_tools, player_tool_calls, player_thinking
