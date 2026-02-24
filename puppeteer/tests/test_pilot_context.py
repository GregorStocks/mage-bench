"""Tests for pilot context window management: summarisation and rendering."""

import json

from puppeteer.pilot import (
    CONTEXT_RECENT_COUNT,
    CONTEXT_SUMMARY_COUNT,
    RENDER_INTERVAL,
    TOOL_RESULT_MAX_CHARS,
    _build_reset_message,
    _extract_last_reasoning,
    _find_tool_name,
    _render_context,
    _summarize_tool_result,
)

# ---------------------------------------------------------------------------
# _summarize_tool_result
# ---------------------------------------------------------------------------


def test_summarize_pass_priority_action_pending():
    content = json.dumps({"action_type": "GAME_SELECT", "action_pending": True})
    result = _summarize_tool_result("pass_priority", content)
    assert "action_pending" in result
    assert "GAME_SELECT" in result
    assert len(result) < 100


def test_summarize_pass_priority_action_pending_with_stop_reason():
    content = json.dumps(
        {
            "action_pending": True,
            "action_type": "GAME_SELECT",
            "stop_reason": "playable_cards",
        }
    )
    result = _summarize_tool_result("pass_priority", content)
    assert "action_pending" in result
    assert "GAME_SELECT" in result
    assert "playable_cards" in result


def test_summarize_pass_priority_passed():
    content = json.dumps({"stop_reason": "passed"})
    result = _summarize_tool_result("pass_priority", content)
    assert "passed" in result


def test_summarize_pass_priority_passed_no_stop_reason():
    """Backwards compatibility: no stop_reason still works."""
    content = json.dumps({})
    result = _summarize_tool_result("pass_priority", content)
    assert "passed" in result


def test_summarize_pass_priority_no_action():
    content = json.dumps({"action_pending": False, "stop_reason": "no_action"})
    result = _summarize_tool_result("pass_priority", content)
    assert "no_action" in result


def test_summarize_pass_priority_reached_step():
    content = json.dumps(
        {
            "action_pending": True,
            "action_type": "GAME_SELECT",
            "current_step": "Declare Attackers",
            "stop_reason": "reached_step",
        }
    )
    result = _summarize_tool_result("pass_priority", content)
    assert "reached_step" in result
    assert "GAME_SELECT" in result


def test_summarize_pass_priority_step_not_reached():
    content = json.dumps(
        {
            "action_pending": True,
            "action_type": "GAME_SELECT",
            "current_step": "Upkeep",
            "stop_reason": "step_not_reached",
        }
    )
    result = _summarize_tool_result("pass_priority", content)
    assert "step_not_reached" in result
    assert "GAME_SELECT" in result


def test_summarize_pass_priority_player_dead():
    content = json.dumps({"player_dead": True})
    assert _summarize_tool_result("pass_priority", content) == "player_dead"


def test_summarize_choose_action_success():
    content = json.dumps({"success": True, "action_taken": "played Lightning Bolt"})
    result = _summarize_tool_result("choose_action", content)
    assert result.startswith("OK:")
    assert "Lightning Bolt" in result


def test_summarize_choose_action_with_mana_plan():
    content = json.dumps({"success": True, "action_taken": "selected_2", "mana_plan_set": True, "mana_plan_size": 3})
    result = _summarize_tool_result("choose_action", content)
    assert result.startswith("OK:")
    assert "mana_plan: 3 entries" in result


def test_summarize_choose_action_failure():
    content = json.dumps({"success": False, "error": "no pending action"})
    result = _summarize_tool_result("choose_action", content)
    assert result.startswith("FAIL:")
    assert "no pending action" in result


def test_summarize_choose_action_failure_with_error_code():
    """Error code and retryable fields should not break existing summarization."""
    content = json.dumps(
        {
            "success": False,
            "error": "Index 5 out of range (call get_action_choices first)",
            "error_code": "index_out_of_range",
            "retryable": True,
        }
    )
    result = _summarize_tool_result("choose_action", content)
    assert result.startswith("FAIL:")
    assert "out of range" in result


def test_summarize_get_action_choices():
    content = json.dumps(
        {
            "action_type": "GAME_SELECT",
            "response_type": "select",
            "choices": [
                {"name": "Mountain", "action": "land"},
                {"name": "Lightning Bolt", "action": "cast", "mana_cost": "{R}", "mana_value": 1},
                {"name": "Goblin Guide", "action": "cast", "mana_cost": "{R}", "mana_value": 1},
            ],
        }
    )
    result = _summarize_tool_result("get_action_choices", content)
    assert "GAME_SELECT" in result
    assert "3 choices" in result
    assert "Mountain" in result
    assert len(result) <= TOOL_RESULT_MAX_CHARS


def test_summarize_get_action_choices_old_format():
    """Old persisted logs use 'description' instead of 'name' — summarizer handles both."""
    content = json.dumps(
        {
            "action_type": "GAME_SELECT",
            "response_type": "select",
            "choices": [
                {"description": "Mountain [Land]"},
                {"description": "Lightning Bolt {R} [Cast]"},
            ],
        }
    )
    result = _summarize_tool_result("get_action_choices", content)
    assert "GAME_SELECT" in result
    assert "2 choices" in result
    assert "Mountain" in result


def test_summarize_get_game_state():
    content = json.dumps(
        {
            "turn": 8,
            "phase": "main1",
            "players": [
                {"name": "Alice", "life": 15, "battlefield": [{"name": "Mountain"}] * 3},
                {"name": "Bob", "life": 12, "battlefield": [{"name": "Forest"}] * 5},
            ],
        }
    )
    result = _summarize_tool_result("get_game_state", content)
    assert "T8" in result
    assert "main1" in result
    assert "Alice:15hp/3perm" in result
    assert "Bob:12hp/5perm" in result
    assert len(result) <= TOOL_RESULT_MAX_CHARS


def test_summarize_get_game_log_basic():
    content = json.dumps(
        {
            "log": "Alice turn 3 (20 - 15)\nAlice casts Sol Ring",
            "total_length": 5234,
            "truncated": False,
            "cursor": 5234,
        }
    )
    result = _summarize_tool_result("get_game_log", content)
    assert "log(" in result
    assert "5234 chars" in result
    assert "Alice turn 3" in result
    assert len(result) <= TOOL_RESULT_MAX_CHARS


def test_summarize_get_game_log_since_turn():
    content = json.dumps(
        {
            "log": "Bob turn 2 (20 - 18)\nBob casts Sol Ring\nAlice turn 3 (20 - 18)\nAlice plays Forest",
            "total_length": 5400,
            "truncated": False,
            "cursor": 5400,
            "since_turn": 2,
            "since_player": "Bob",
        }
    )
    result = _summarize_tool_result("get_game_log", content)
    assert "since_turn=2" in result
    assert "Bob turn 2" in result
    assert len(result) <= TOOL_RESULT_MAX_CHARS


def test_summarize_get_game_log_truncated():
    content = json.dumps(
        {
            "log": "Alice turn 2 (20 - 18)\nAlice attacks with Goblin Guide",
            "total_length": 10000,
            "truncated": True,
            "cursor": 10000,
            "since_turn": 1,
            "since_player": "Alice",
        }
    )
    result = _summarize_tool_result("get_game_log", content)
    assert "truncated" in result
    assert "since_turn=1" in result
    assert len(result) <= TOOL_RESULT_MAX_CHARS


def test_summarize_get_game_log_empty():
    content = json.dumps(
        {
            "log": "",
            "total_length": 0,
            "truncated": False,
            "cursor": 0,
        }
    )
    result = _summarize_tool_result("get_game_log", content)
    assert "log(" in result
    assert "0 chars" in result


def test_summarize_invalid_json():
    result = _summarize_tool_result("get_game_state", "not valid json at all")
    assert result == "not valid json at all"[:TOOL_RESULT_MAX_CHARS]


def test_summarize_already_small():
    content = json.dumps({"success": True})
    result = _summarize_tool_result("send_chat_message", content)
    assert result == content[:TOOL_RESULT_MAX_CHARS]


# ---------------------------------------------------------------------------
# _find_tool_name
# ---------------------------------------------------------------------------


def _make_assistant_msg(tool_calls: list[tuple[str, str]]) -> dict:
    """Helper: build an assistant message with tool_calls."""
    return {
        "role": "assistant",
        "content": "thinking...",
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}
            for call_id, name in tool_calls
        ],
    }


def _make_tool_msg(call_id: str, content: str = "{}") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def test_find_tool_name_basic():
    history = [
        _make_assistant_msg([("call_1", "pass_priority"), ("call_2", "get_action_choices")]),
        _make_tool_msg("call_1"),
        _make_tool_msg("call_2"),
    ]
    assert _find_tool_name(history, 1, "call_1") == "pass_priority"
    assert _find_tool_name(history, 2, "call_2") == "get_action_choices"


def test_find_tool_name_missing():
    history = [
        _make_assistant_msg([("call_1", "pass_priority")]),
        _make_tool_msg("call_999"),
    ]
    assert _find_tool_name(history, 1, "call_999") == ""


def test_find_tool_name_no_assistant():
    history = [
        {"role": "user", "content": "hello"},
        _make_tool_msg("call_1"),
    ]
    assert _find_tool_name(history, 1, "call_1") == ""


# ---------------------------------------------------------------------------
# _render_context
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = "You are a test pilot."
STATE_SUMMARY = "Turn 5; Alice: 20hp. "


def _make_history(n: int) -> list[dict]:
    """Build a history of n messages with alternating assistant+tool pairs."""
    history = [{"role": "user", "content": "Start the game."}]
    call_idx = 0
    while len(history) < n:
        call_id = f"call_{call_idx}"
        history.append(_make_assistant_msg([(call_id, "pass_priority")]))
        history.append(_make_tool_msg(call_id, json.dumps({"timeout": True})))
        call_idx += 1
    return history[:n]


def test_render_short_history():
    """Under threshold: all messages at full fidelity, no state bridge."""
    history = _make_history(5)
    messages = _render_context(history, SYSTEM_PROMPT, STATE_SUMMARY)
    # system prompt + all 5 history entries
    assert len(messages) == 6
    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    # History messages should be unchanged
    for i, msg in enumerate(history):
        assert messages[i + 1] == msg


def test_render_long_history_summarizes_old():
    """Over threshold: old tool results get summarised."""
    n = CONTEXT_RECENT_COUNT + CONTEXT_SUMMARY_COUNT + 10
    history = _make_history(n)
    messages = _render_context(history, SYSTEM_PROMPT, STATE_SUMMARY)

    # Should have: system + summarised slice + state bridge + recent slice
    assert messages[0]["role"] == "system"

    # Find state bridge by content (position varies due to boundary adjustment)
    bridge_idx = None
    for i, msg in enumerate(messages):
        if msg.get("role") == "user" and STATE_SUMMARY in msg.get("content", ""):
            bridge_idx = i
            break
    assert bridge_idx is not None, "State bridge not found"

    # Find tool messages in the summarised section (between system and bridge)
    summarised_section = messages[1:bridge_idx]
    for msg in summarised_section:
        if msg["role"] == "tool":
            # Should be summarised (short)
            assert len(msg["content"]) <= TOOL_RESULT_MAX_CHARS


def test_render_preserves_recent_full():
    """Last CONTEXT_RECENT_COUNT messages should be at full fidelity."""
    n = CONTEXT_RECENT_COUNT + CONTEXT_SUMMARY_COUNT + 10
    history = _make_history(n)
    messages = _render_context(history, SYSTEM_PROMPT, STATE_SUMMARY)

    # The last CONTEXT_RECENT_COUNT messages should match history exactly
    recent_history = history[-CONTEXT_RECENT_COUNT:]
    recent_rendered = messages[-CONTEXT_RECENT_COUNT:]
    assert recent_history == recent_rendered


def test_render_includes_state_summary():
    """State bridge message should be present after summarised section, before recent."""
    history = _make_history(CONTEXT_RECENT_COUNT + 5)
    messages = _render_context(history, SYSTEM_PROMPT, STATE_SUMMARY)
    # Find the state bridge by content
    bridge = None
    bridge_idx = None
    for i, msg in enumerate(messages):
        if msg.get("role") == "user" and STATE_SUMMARY in msg.get("content", ""):
            bridge = msg
            bridge_idx = i
            break
    assert bridge is not None, "State bridge not found"
    assert bridge_idx > 1, f"State bridge at position {bridge_idx}, expected after summarised section"
    assert "pass_priority" in bridge["content"]


def test_render_no_orphaned_tool_results():
    """Every tool message in rendered output should have its assistant pair."""
    n = CONTEXT_RECENT_COUNT + CONTEXT_SUMMARY_COUNT + 10
    history = _make_history(n)
    messages = _render_context(history, SYSTEM_PROMPT, STATE_SUMMARY)

    # Check that every tool message has its tool_call_id in a preceding assistant message
    seen_call_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []):
                seen_call_ids.add(tc["id"])
        elif msg.get("role") == "tool":
            assert msg["tool_call_id"] in seen_call_ids, (
                f"Orphaned tool result: {msg['tool_call_id']} not in any preceding assistant message"
            )


# ---------------------------------------------------------------------------
# _extract_last_reasoning
# ---------------------------------------------------------------------------


def test_extract_last_reasoning_basic():
    history = [
        {"role": "user", "content": "Start"},
        {"role": "assistant", "content": "First thought"},
        {"role": "assistant", "content": "Second thought"},
    ]
    assert _extract_last_reasoning(history) == "Second thought"


def test_extract_last_reasoning_skips_tool_messages():
    history = [
        {"role": "assistant", "content": "My plan"},
        _make_tool_msg("call_1", "{}"),
    ]
    assert _extract_last_reasoning(history) == "My plan"


def test_extract_last_reasoning_empty_history():
    assert _extract_last_reasoning([]) == ""


def test_extract_last_reasoning_no_assistant():
    history = [{"role": "user", "content": "hello"}]
    assert _extract_last_reasoning(history) == ""


def test_extract_last_reasoning_truncates():
    history = [{"role": "assistant", "content": "x" * 500}]
    result = _extract_last_reasoning(history)
    assert len(result) == 300


def test_extract_last_reasoning_skips_none_content():
    history = [
        {"role": "assistant", "content": "Good thought"},
        {"role": "assistant", "content": None},
    ]
    assert _extract_last_reasoning(history) == "Good thought"


# ---------------------------------------------------------------------------
# _build_reset_message
# ---------------------------------------------------------------------------


def test_build_reset_message_base_only():
    result = _build_reset_message("Continue playing.", "")
    assert result == "Continue playing."


def test_build_reset_message_with_reasoning():
    result = _build_reset_message("Continue.", "I was about to attack")
    assert "Continue." in result
    assert "Before your context was reset, you were thinking: I was about to attack" in result


# ---------------------------------------------------------------------------
# State bridge position (prompt caching)
# ---------------------------------------------------------------------------


def test_render_state_bridge_after_summarized():
    """State bridge should appear after summarized section, before recent window."""
    n = CONTEXT_RECENT_COUNT + CONTEXT_SUMMARY_COUNT + 10
    history = _make_history(n)
    messages = _render_context(history, SYSTEM_PROMPT, STATE_SUMMARY)

    # Find the state bridge by content
    bridge_idx = None
    for i, msg in enumerate(messages):
        if msg.get("role") == "user" and STATE_SUMMARY in msg.get("content", ""):
            bridge_idx = i
            break
    assert bridge_idx is not None, "State bridge not found in rendered messages"

    # Should not be at position 1 (old behavior) — must be after summarized section
    assert bridge_idx > 1, f"State bridge at position {bridge_idx}, expected after summarized section"

    # Should be right before the recent window
    recent_messages = messages[bridge_idx + 1 :]
    assert len(recent_messages) >= CONTEXT_RECENT_COUNT


# ---------------------------------------------------------------------------
# Cached prefix reuse
# ---------------------------------------------------------------------------


def test_cached_prefix_reuse_concept():
    """Verify that cached_render + new_history produces valid messages."""
    n = CONTEXT_RECENT_COUNT + CONTEXT_SUMMARY_COUNT + 10
    history = _make_history(n)
    messages = _render_context(history, SYSTEM_PROMPT, STATE_SUMMARY)
    cached_render = list(messages)
    cached_history_len = len(history)

    # Simulate 2 new history entries (assistant + tool pair)
    new_entries = [
        _make_assistant_msg([("call_new_1", "pass_priority")]),
        _make_tool_msg("call_new_1", json.dumps({"timeout": True})),
    ]
    history.extend(new_entries)

    # Reuse cached prefix + new entries
    reused = cached_render + history[cached_history_len:]
    assert reused[: len(cached_render)] == cached_render
    assert reused[len(cached_render) :] == new_entries

    # Verify no orphaned tool results in the combined output
    seen_call_ids: set[str] = set()
    for msg in reused:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []):
                seen_call_ids.add(tc["id"])
        elif msg.get("role") == "tool":
            assert msg["tool_call_id"] in seen_call_ids


def test_render_interval_constant():
    """RENDER_INTERVAL should be a positive integer."""
    assert isinstance(RENDER_INTERVAL, int)
    assert RENDER_INTERVAL > 0


# ---------------------------------------------------------------------------
# pass_priority with inline choices (merged from get_action_choices)
# ---------------------------------------------------------------------------


def test_summarize_pass_priority_with_choices():
    """pass_priority now returns choices inline when action_pending=true."""
    content = json.dumps(
        {
            "action_pending": True,
            "action_type": "GAME_SELECT",
            "stop_reason": "playable_cards",
            "response_type": "select",
            "choices": [
                {"index": 0, "name": "Lightning Bolt", "action": "cast", "mana_cost": "{R}"},
                {"index": 1, "name": "Mountain", "action": "land"},
            ],
            "context": "T3 PRECOMBAT_MAIN (Player1) YOUR_MAIN",
            "players": "You(20), Opp(18)",
            "untapped_lands": 2,
        }
    )
    result = _summarize_tool_result("pass_priority", content)
    assert "action_pending" in result
    assert "GAME_SELECT" in result
    assert "playable_cards" in result
    assert "select" in result
    assert "2 choices" in result
    assert "Lightning Bolt" in result


def test_summarize_pass_priority_with_message_no_choices():
    """Non-priority actions have a message but no choices list."""
    content = json.dumps(
        {
            "action_pending": True,
            "action_type": "GAME_ASK",
            "stop_reason": "non_priority_action",
            "response_type": "boolean",
            "message": "Mulligan hand?",
        }
    )
    result = _summarize_tool_result("pass_priority", content)
    assert "action_pending" in result
    assert "GAME_ASK" in result
    assert "boolean" in result
    assert "Mulligan" in result


# ---------------------------------------------------------------------------
# _render_context with cache_control
# ---------------------------------------------------------------------------


def test_render_cache_control_content_block():
    """With cache_control, system message uses content block array format."""
    history = _make_history(5)
    cc = {"type": "ephemeral"}
    messages = _render_context(history, SYSTEM_PROMPT, STATE_SUMMARY, cache_control=cc)
    sys_msg = messages[0]
    assert sys_msg["role"] == "system"
    assert isinstance(sys_msg["content"], list)
    assert len(sys_msg["content"]) == 1
    block = sys_msg["content"][0]
    assert block["type"] == "text"
    assert block["text"] == SYSTEM_PROMPT
    assert block["cache_control"] == {"type": "ephemeral"}


def test_render_no_cache_control_plain_string():
    """Without cache_control, system message uses plain string format."""
    history = _make_history(5)
    messages = _render_context(history, SYSTEM_PROMPT, STATE_SUMMARY, cache_control=None)
    sys_msg = messages[0]
    assert sys_msg["role"] == "system"
    assert sys_msg["content"] == SYSTEM_PROMPT


def test_render_cache_control_with_no_strategy():
    """cache_control without strategy: system prompt used directly."""
    history = _make_history(5)
    cc = {"type": "ephemeral"}
    messages = _render_context(history, SYSTEM_PROMPT, STATE_SUMMARY, cache_control=cc)
    sys_msg = messages[0]
    assert isinstance(sys_msg["content"], list)
    block = sys_msg["content"][0]
    assert block["text"] == SYSTEM_PROMPT
    assert block["cache_control"] == {"type": "ephemeral"}
