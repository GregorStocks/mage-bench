"""Tests for game export wire-format migrations."""

from __future__ import annotations

from schemas.game_export_migrations import (
    CURRENT_GAME_EXPORT_VERSION,
    LEGACY_GAME_EXPORT_VERSION,
    demigrate_game_export_v9_to_v8,
    migrate_game_export_v8_to_v9,
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


def _minimal_v8_export(**overrides) -> dict[str, object]:
    return demigrate_game_export_v9_to_v8(_minimal_v9_export(**overrides))


def test_migrate_game_export_v8_to_v9_renames_wire_keys() -> None:
    migrated = migrate_game_export_v8_to_v9(_minimal_v8_export())

    assert migrated["version"] == CURRENT_GAME_EXPORT_VERSION
    assert migrated["game_type"] == "Two Player Duel"
    assert "gameType" not in migrated
    assert migrated["players"][0]["tool_calls_ok"] == 3
    assert "toolCallsOk" not in migrated["players"][0]
    assert migrated["llm_events"][0]["available_tools"] == ["pass_priority"]
    assert migrated["llm_events"][1]["tool_calls"] == []
    assert migrated["llm_events"][1]["usage"]["prompt_tokens"] == 10
    assert migrated["llm_events"][2]["latency_ms"] == 123
    assert migrated["annotations"][0]["decision_index"] == 0
    assert migrated["decisions"][0]["pilot_context"]["untapped_lands"] == 1
    assert migrated["errors"][0]["decision_index"] == 0


def test_demigrate_game_export_v9_to_v8_restores_legacy_keys() -> None:
    demigrated = demigrate_game_export_v9_to_v8(_minimal_v9_export())

    assert demigrated["version"] == LEGACY_GAME_EXPORT_VERSION
    assert demigrated["gameType"] == "Two Player Duel"
    assert "game_type" not in demigrated
    assert demigrated["players"][0]["toolCallsOk"] == 3
    assert demigrated["llmEvents"][0]["availableTools"] == ["pass_priority"]
    assert demigrated["llmEvents"][1]["toolCalls"] == []
    assert demigrated["llmEvents"][1]["usage"]["promptTokens"] == 10
    assert demigrated["llmEvents"][2]["latencyMs"] == 123
    assert demigrated["annotations"][0]["decisionIndex"] == 0
    assert demigrated["decisions"][0]["pilotContext"]["untappedLands"] == 1
    assert demigrated["errors"][0]["decisionIndex"] == 0


def test_v8_v9_round_trip_preserves_legacy_export_shape() -> None:
    legacy = _minimal_v8_export()

    assert demigrate_game_export_v9_to_v8(migrate_game_export_v8_to_v9(legacy)) == legacy
