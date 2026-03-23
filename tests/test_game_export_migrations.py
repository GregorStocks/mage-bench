"""Tests for strict game export wire-format handling."""

from __future__ import annotations

import pytest

from schemas.game_export_migrations import (
    CURRENT_GAME_EXPORT_VERSION,
    migrate_game_export_to_current,
)


def _minimal_v9_export(**overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": CURRENT_GAME_EXPORT_VERSION,
        "id": "game_test",
        "timestamp": "",
        "game_type": "Two Player Duel",
        "deck_type": "Constructed - Standard",
        "total_turns": 1,
        "winner": None,
        "harness_epoch": 0,
        "youtube_url": "",
        "players": [
            {
                "name": "Alice",
                "type": "pilot",
                "model": "test/model",
                "tool_calls_ok": 3,
                "tool_calls_failed": 1,
                "thinking_time_secs": 12.5,
                "deck_name": "Azorius Control",
                "reasoning_effort": "medium",
                "total_cost_usd": 0.1234,
                "timed_out": False,
            }
        ],
        "card_images": {},
        "snapshots": [],
        "actions": [],
        "llm_events": [
            {
                "type": "game_start",
                "player": "Alice",
                "model": "test/model",
                "game_seq": 1,
                "available_tools": ["pass_priority"],
            },
            {
                "type": "llm_response",
                "player": "Alice",
                "seq": 2,
                "game_seq": 2,
                "tool_calls": [],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "cached_tokens": 0,
                    "reasoning_tokens": 2,
                },
                "cost_usd": 0.01,
            },
            {
                "type": "tool_call",
                "player": "Alice",
                "seq": 3,
                "game_seq": 3,
                "tool": "choose_action",
                "args": {"choice": "p1"},
                "result": "{}",
                "latency_ms": 123,
            },
            {
                "type": "stall",
                "player": "Alice",
                "turns_without_progress": 2,
                "last_tools": ["choose_action"],
            },
            {
                "type": "context_trim",
                "player": "Alice",
                "messages_before": 12,
                "messages_after": 8,
            },
            {
                "type": "llm_error",
                "player": "Alice",
                "error_type": "rate_limit",
                "error_message": "try again",
            },
        ],
        "game_over": None,
        "annotations": [
            {
                "decision_index": 0,
                "snapshot_index": 0,
                "player": "Alice",
                "type": "blunder",
                "severity": "minor",
                "description": "Missed line",
                "action_taken": "Pass",
                "better_line": "Cast threat",
                "llm_reasoning": "low confidence",
            }
        ],
        "blunder_script_version": 1,
        "season": 1,
        "tournament": None,
        "decisions": [
            {
                "index": 0,
                "snapshot_index": 0,
                "player": "Alice",
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "action_type": "play",
                "response_type": "choice",
                "message": "Play spells and abilities",
                "choices": [],
                "choice_count": 0,
                "is_forced": True,
                "llm_event_indices": [0, 1],
                "subsequent_actions": [],
                "pilot_context": {
                    "untapped_lands": 1,
                    "land_drops_used": 0,
                    "playable_cards": ["Memnite"],
                    "combat_phase": None,
                    "already_attacking": [],
                    "incoming_attackers": [],
                },
                "chosen_args": {"choice": "p1"},
                "action_result": {"success": True},
                "cast_rolled_back": False,
                "items": [{"description": "Assign damage", "min": 0, "max": 1}],
                "total_min": 0,
                "total_max": 1,
                "action_seq": 10,
            }
        ],
        "errors": [
            {
                "ts": "2026-03-20T12:00:00Z",
                "player": "Alice",
                "source": "pilot",
                "message": "boom",
                "decision_index": 0,
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_migrate_game_export_to_current_accepts_v9_payload() -> None:
    migrated = migrate_game_export_to_current(_minimal_v9_export())

    assert migrated["version"] == CURRENT_GAME_EXPORT_VERSION
    assert migrated["players"][0]["tool_calls_ok"] == 3
    assert migrated["llm_events"][0]["available_tools"] == ["pass_priority"]
    assert migrated["annotations"][0]["decision_index"] == 0
    assert migrated["decisions"][0]["pilot_context"]["untapped_lands"] == 1
    assert migrated["errors"][0]["decision_index"] == 0


def test_migrate_game_export_to_current_rejects_legacy_versions() -> None:
    legacy = _minimal_v9_export(version=8)

    with pytest.raises(AssertionError, match="Unsupported game export version 8; expected 9"):
        migrate_game_export_to_current(legacy)
