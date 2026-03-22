"""Game export wire-format migrations.

These helpers convert committed exports between the legacy v8 camelCase wire
format and the v9 snake_case wire format.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping

JsonValue = object
JsonObject = dict[str, JsonValue]

CURRENT_GAME_EXPORT_VERSION = 9
LEGACY_GAME_EXPORT_VERSION = 8

_TOP_LEVEL_V8_TO_V9 = {
    "gameType": "game_type",
    "deckType": "deck_type",
    "totalTurns": "total_turns",
    "harnessEpoch": "harness_epoch",
    "youtubeUrl": "youtube_url",
    "cardImages": "card_images",
    "cardData": "card_data",
    "llmEvents": "llm_events",
    "gameOver": "game_over",
    "blunderScriptVersion": "blunder_script_version",
}

_PLAYER_V8_TO_V9 = {
    "deckName": "deck_name",
    "deckStrategy": "deck_strategy",
    "reasoningEffort": "reasoning_effort",
    "totalCostUsd": "total_cost_usd",
    "toolCallsOk": "tool_calls_ok",
    "toolCallsFailed": "tool_calls_failed",
    "thinkingTimeSecs": "thinking_time_secs",
    "timedOut": "timed_out",
}

_LLM_EVENT_BASE_V8_TO_V9 = {
    "gameSeq": "game_seq",
}

_GAME_START_EVENT_V8_TO_V9 = {
    "availableTools": "available_tools",
}

_LLM_RESPONSE_EVENT_V8_TO_V9 = {
    "toolCalls": "tool_calls",
    "costUsd": "cost_usd",
}

_TOOL_CALL_EVENT_V8_TO_V9 = {
    "latencyMs": "latency_ms",
}

_STALL_EVENT_V8_TO_V9 = {
    "turnsWithoutProgress": "turns_without_progress",
    "lastTools": "last_tools",
}

_CONTEXT_TRIM_EVENT_V8_TO_V9 = {
    "messagesBefore": "messages_before",
    "messagesAfter": "messages_after",
}

_LLM_ERROR_EVENT_V8_TO_V9 = {
    "errorType": "error_type",
    "errorMessage": "error_message",
}

_LLM_USAGE_V8_TO_V9 = {
    "promptTokens": "prompt_tokens",
    "completionTokens": "completion_tokens",
    "cachedTokens": "cached_tokens",
    "reasoningTokens": "reasoning_tokens",
}

_ANNOTATION_V8_TO_V9 = {
    "decisionIndex": "decision_index",
    "snapshotIndex": "snapshot_index",
    "actionTaken": "action_taken",
    "betterLine": "better_line",
    "llmReasoning": "llm_reasoning",
}

_DECISION_V8_TO_V9 = {
    "snapshotIndex": "snapshot_index",
    "actionType": "action_type",
    "responseType": "response_type",
    "choiceCount": "choice_count",
    "isForced": "is_forced",
    "llmEventIndices": "llm_event_indices",
    "subsequentActions": "subsequent_actions",
    "pilotContext": "pilot_context",
    "chosenArgs": "chosen_args",
    "actionResult": "action_result",
    "castRolledBack": "cast_rolled_back",
    "totalMin": "total_min",
    "totalMax": "total_max",
    "actionSeq": "action_seq",
}

_PILOT_CONTEXT_V8_TO_V9 = {
    "untappedLands": "untapped_lands",
    "landDropsUsed": "land_drops_used",
    "playableCards": "playable_cards",
    "combatPhase": "combat_phase",
    "alreadyAttacking": "already_attacking",
    "incomingAttackers": "incoming_attackers",
}

_GAME_ERROR_V8_TO_V9 = {
    "decisionIndex": "decision_index",
}


def _invert(mapping: Mapping[str, str]) -> dict[str, str]:
    return {value: key for key, value in mapping.items()}


_TOP_LEVEL_V9_TO_V8 = _invert(_TOP_LEVEL_V8_TO_V9)
_PLAYER_V9_TO_V8 = _invert(_PLAYER_V8_TO_V9)
_LLM_EVENT_BASE_V9_TO_V8 = _invert(_LLM_EVENT_BASE_V8_TO_V9)
_GAME_START_EVENT_V9_TO_V8 = _invert(_GAME_START_EVENT_V8_TO_V9)
_LLM_RESPONSE_EVENT_V9_TO_V8 = _invert(_LLM_RESPONSE_EVENT_V8_TO_V9)
_TOOL_CALL_EVENT_V9_TO_V8 = _invert(_TOOL_CALL_EVENT_V8_TO_V9)
_STALL_EVENT_V9_TO_V8 = _invert(_STALL_EVENT_V8_TO_V9)
_CONTEXT_TRIM_EVENT_V9_TO_V8 = _invert(_CONTEXT_TRIM_EVENT_V8_TO_V9)
_LLM_ERROR_EVENT_V9_TO_V8 = _invert(_LLM_ERROR_EVENT_V8_TO_V9)
_LLM_USAGE_V9_TO_V8 = _invert(_LLM_USAGE_V8_TO_V9)
_ANNOTATION_V9_TO_V8 = _invert(_ANNOTATION_V8_TO_V9)
_DECISION_V9_TO_V8 = _invert(_DECISION_V8_TO_V9)
_PILOT_CONTEXT_V9_TO_V8 = _invert(_PILOT_CONTEXT_V8_TO_V9)
_GAME_ERROR_V9_TO_V8 = _invert(_GAME_ERROR_V8_TO_V9)


def _rename_keys(
    obj: Mapping[str, JsonValue], mapping: Mapping[str, str]
) -> JsonObject:
    return {mapping.get(key, key): value for key, value in obj.items()}


def _copy_list(values: object) -> list[object]:
    assert isinstance(values, list), f"Expected list, got {values!r}"
    return [copy.deepcopy(value) for value in values]


def _copy_dict(value: object) -> JsonObject:
    assert isinstance(value, Mapping), f"Expected object, got {value!r}"
    return {str(key): copy.deepcopy(item) for key, item in value.items()}


def _migrate_player_v8_to_v9(player: object) -> JsonObject:
    return _rename_keys(_copy_dict(player), _PLAYER_V8_TO_V9)


def _migrate_player_v9_to_v8(player: object) -> JsonObject:
    return _rename_keys(_copy_dict(player), _PLAYER_V9_TO_V8)


def _migrate_llm_usage_v8_to_v9(usage: object) -> JsonObject:
    return _rename_keys(_copy_dict(usage), _LLM_USAGE_V8_TO_V9)


def _migrate_llm_usage_v9_to_v8(usage: object) -> JsonObject:
    return _rename_keys(_copy_dict(usage), _LLM_USAGE_V9_TO_V8)


def _migrate_llm_event_v8_to_v9(event: object) -> JsonObject:
    migrated = _rename_keys(_copy_dict(event), _LLM_EVENT_BASE_V8_TO_V9)
    event_type = migrated.get("type")
    if event_type == "game_start":
        migrated = _rename_keys(migrated, _GAME_START_EVENT_V8_TO_V9)
    elif event_type == "llm_response":
        migrated = _rename_keys(migrated, _LLM_RESPONSE_EVENT_V8_TO_V9)
        if "usage" in migrated and migrated["usage"] is not None:
            migrated["usage"] = _migrate_llm_usage_v8_to_v9(migrated["usage"])
    elif event_type == "tool_call":
        migrated = _rename_keys(migrated, _TOOL_CALL_EVENT_V8_TO_V9)
    elif event_type == "stall":
        migrated = _rename_keys(migrated, _STALL_EVENT_V8_TO_V9)
    elif event_type == "context_trim":
        migrated = _rename_keys(migrated, _CONTEXT_TRIM_EVENT_V8_TO_V9)
    elif event_type == "llm_error":
        migrated = _rename_keys(migrated, _LLM_ERROR_EVENT_V8_TO_V9)
    return migrated


def _migrate_llm_event_v9_to_v8(event: object) -> JsonObject:
    migrated = _rename_keys(_copy_dict(event), _LLM_EVENT_BASE_V9_TO_V8)
    event_type = migrated.get("type")
    if event_type == "game_start":
        migrated = _rename_keys(migrated, _GAME_START_EVENT_V9_TO_V8)
    elif event_type == "llm_response":
        migrated = _rename_keys(migrated, _LLM_RESPONSE_EVENT_V9_TO_V8)
        if "usage" in migrated and migrated["usage"] is not None:
            migrated["usage"] = _migrate_llm_usage_v9_to_v8(migrated["usage"])
    elif event_type == "tool_call":
        migrated = _rename_keys(migrated, _TOOL_CALL_EVENT_V9_TO_V8)
    elif event_type == "stall":
        migrated = _rename_keys(migrated, _STALL_EVENT_V9_TO_V8)
    elif event_type == "context_trim":
        migrated = _rename_keys(migrated, _CONTEXT_TRIM_EVENT_V9_TO_V8)
    elif event_type == "llm_error":
        migrated = _rename_keys(migrated, _LLM_ERROR_EVENT_V9_TO_V8)
    return migrated


def _migrate_annotation_v8_to_v9(annotation: object) -> JsonObject:
    return _rename_keys(_copy_dict(annotation), _ANNOTATION_V8_TO_V9)


def _migrate_annotation_v9_to_v8(annotation: object) -> JsonObject:
    return _rename_keys(_copy_dict(annotation), _ANNOTATION_V9_TO_V8)


def _migrate_pilot_context_v8_to_v9(context: object) -> JsonObject:
    return _rename_keys(_copy_dict(context), _PILOT_CONTEXT_V8_TO_V9)


def _migrate_pilot_context_v9_to_v8(context: object) -> JsonObject:
    return _rename_keys(_copy_dict(context), _PILOT_CONTEXT_V9_TO_V8)


def _migrate_decision_v8_to_v9(decision: object) -> JsonObject:
    migrated = _rename_keys(_copy_dict(decision), _DECISION_V8_TO_V9)
    if "pilot_context" in migrated and migrated["pilot_context"] is not None:
        migrated["pilot_context"] = _migrate_pilot_context_v8_to_v9(
            migrated["pilot_context"]
        )
    return migrated


def _migrate_decision_v9_to_v8(decision: object) -> JsonObject:
    migrated = _rename_keys(_copy_dict(decision), _DECISION_V9_TO_V8)
    if "pilotContext" in migrated and migrated["pilotContext"] is not None:
        migrated["pilotContext"] = _migrate_pilot_context_v9_to_v8(
            migrated["pilotContext"]
        )
    return migrated


def _migrate_game_error_v8_to_v9(error: object) -> JsonObject:
    return _rename_keys(_copy_dict(error), _GAME_ERROR_V8_TO_V9)


def _migrate_game_error_v9_to_v8(error: object) -> JsonObject:
    return _rename_keys(_copy_dict(error), _GAME_ERROR_V9_TO_V8)


def migrate_game_export_v8_to_v9(data: Mapping[str, JsonValue]) -> JsonObject:
    """Convert a legacy v8 export into the v9 snake_case wire format."""
    migrated = _rename_keys(_copy_dict(data), _TOP_LEVEL_V8_TO_V9)
    version = migrated.get("version")
    assert version == LEGACY_GAME_EXPORT_VERSION, (
        f"Expected v{LEGACY_GAME_EXPORT_VERSION}, got {version!r}"
    )
    migrated["version"] = CURRENT_GAME_EXPORT_VERSION
    if "players" in migrated:
        migrated["players"] = [
            _migrate_player_v8_to_v9(player)
            for player in _copy_list(migrated["players"])
        ]
    if "llm_events" in migrated:
        migrated["llm_events"] = [
            _migrate_llm_event_v8_to_v9(event)
            for event in _copy_list(migrated["llm_events"])
        ]
    if "annotations" in migrated:
        migrated["annotations"] = [
            _migrate_annotation_v8_to_v9(annotation)
            for annotation in _copy_list(migrated["annotations"])
        ]
    if "decisions" in migrated:
        migrated["decisions"] = [
            _migrate_decision_v8_to_v9(decision)
            for decision in _copy_list(migrated["decisions"])
        ]
    if "errors" in migrated:
        migrated["errors"] = [
            _migrate_game_error_v8_to_v9(error)
            for error in _copy_list(migrated["errors"])
        ]
    return migrated


def demigrate_game_export_v9_to_v8(data: Mapping[str, JsonValue]) -> JsonObject:
    """Convert a v9 snake_case export into the legacy v8 camelCase wire format."""
    migrated = _rename_keys(_copy_dict(data), _TOP_LEVEL_V9_TO_V8)
    version = migrated.get("version")
    assert version == CURRENT_GAME_EXPORT_VERSION, (
        f"Expected v{CURRENT_GAME_EXPORT_VERSION}, got {version!r}"
    )
    migrated["version"] = LEGACY_GAME_EXPORT_VERSION
    if "players" in migrated:
        migrated["players"] = [
            _migrate_player_v9_to_v8(player)
            for player in _copy_list(migrated["players"])
        ]
    if "llmEvents" in migrated:
        migrated["llmEvents"] = [
            _migrate_llm_event_v9_to_v8(event)
            for event in _copy_list(migrated["llmEvents"])
        ]
    if "annotations" in migrated:
        migrated["annotations"] = [
            _migrate_annotation_v9_to_v8(annotation)
            for annotation in _copy_list(migrated["annotations"])
        ]
    if "decisions" in migrated:
        migrated["decisions"] = [
            _migrate_decision_v9_to_v8(decision)
            for decision in _copy_list(migrated["decisions"])
        ]
    if "errors" in migrated:
        migrated["errors"] = [
            _migrate_game_error_v9_to_v8(error)
            for error in _copy_list(migrated["errors"])
        ]
    return migrated


def migrate_game_export_to_current(data: Mapping[str, JsonValue]) -> JsonObject:
    """Normalize a supported export payload to the current wire format."""
    version = data.get("version")
    assert isinstance(version, int), (
        f"game export version must be an int, got {version!r}"
    )
    if version == CURRENT_GAME_EXPORT_VERSION:
        return _copy_dict(data)
    if version == LEGACY_GAME_EXPORT_VERSION:
        return migrate_game_export_v8_to_v9(data)
    raise AssertionError(
        "Unsupported game export version "
        f"{version}; expected {LEGACY_GAME_EXPORT_VERSION} or {CURRENT_GAME_EXPORT_VERSION}"
    )


__all__ = [
    "CURRENT_GAME_EXPORT_VERSION",
    "LEGACY_GAME_EXPORT_VERSION",
    "demigrate_game_export_v9_to_v8",
    "migrate_game_export_to_current",
    "migrate_game_export_v8_to_v9",
]
