#!/usr/bin/env python3
"""Extract LLM decision points from a game export (.json or .json.gz).

For each meaningful decision (get_action_choices -> llm_response -> choose_action),
outputs the game state, available choices, what was chosen, LLM reasoning, and
what happened next. Designed to give Claude Code structured data for blunder analysis.
"""

import json
import sys
from collections.abc import Sequence

from schemas.game_export_types import (
    BuiltGameExport,
    CombatGroup,
    Decision,
    JsonObject,
    LlmEvent,
    Snapshot,
    ToolCallEvent,
    export_record_field,
    json_default,
)
from scripts.analysis.blunder_eval_common import load_game_for_annotation


def _record_field(record: object, field: str) -> object | None:
    return export_record_field(record, field)


def _record_name(record: object, *, source: str) -> str:
    name = _record_field(record, "name")
    assert isinstance(name, str), f"{source} name must be a string, got {name!r}"
    return name


def _summarize_permanent(c: object) -> str | dict:
    """Summarize a battlefield permanent. Returns just the name if nothing
    interesting, or a dict with extra info when tapped/counters/sick."""
    if isinstance(c, str):
        return str(c)
    name = _record_name(c, source="permanent")
    extras: dict = {}
    if _record_field(c, "tapped"):
        extras["tapped"] = True
    if _record_field(c, "summoning_sick"):
        extras["summoning_sick"] = True
    counters = _record_field(c, "counters")
    if counters:
        extras["counters"] = counters
    if _record_field(c, "token"):
        extras["token"] = True
    if _record_field(c, "face_down"):
        extras["face_down"] = True
    if _record_field(c, "copy"):
        extras["copy"] = True
    original_card = _record_field(c, "original_card")
    if isinstance(original_card, str) and original_card:
        extras["original_card"] = original_card
    rules = _record_field(c, "rules")
    if rules is not None:
        extras["rules"] = rules
    if extras:
        return {"name": name, **extras}
    return name


def _summarize_stack_item(item: object) -> str | dict:
    """Summarize a stack item. Returns just the name if no targets,
    or a dict with name + targets when targets are present."""
    if isinstance(item, str):
        return str(item)
    name = _record_name(item, source="stack item")
    targets = _record_field(item, "targets")
    if targets:
        assert isinstance(targets, list), (
            f"stack item targets must be a list, got {targets!r}"
        )
        return {"name": name, "targets": [_summarize_stack_target(t) for t in targets]}
    return name


def _summarize_stack_target(target: object) -> str | dict:
    if isinstance(target, str):
        return target
    name = _record_field(target, "name")
    target_id = _record_field(target, "id")
    summary: dict[str, object] = {}
    if isinstance(name, str) and name:
        summary["name"] = name
    if isinstance(target_id, str) and target_id:
        summary["id"] = target_id
    return summary if summary else str(target)


def _summarize_combat_group(group: CombatGroup) -> dict[str, object]:
    summary: dict[str, object] = {}
    if group.attackers is not None:
        summary["attackers"] = [_summarize_combat_creature(a) for a in group.attackers]
    if group.blockers is not None:
        summary["blockers"] = [_summarize_combat_creature(b) for b in group.blockers]
    if group.blocked is not None:
        summary["blocked"] = group.blocked
    if group.defending is not None:
        summary["defending"] = group.defending
    return summary


def _summarize_combat_creature(creature: object) -> dict[str, object]:
    name = _record_name(creature, source="combat creature")
    summary: dict[str, object] = {"name": name}
    for field in ("id", "power", "toughness", "power_toughness", "pt"):
        value = _record_field(creature, field)
        if value is not None:
            summary[field] = value
    return summary


def _summarize_snapshot(snap: Snapshot) -> dict[str, object]:
    """Summarize a snapshot for decision context."""
    players_summary: list[dict[str, object]] = []
    for p in snap.players:
        p_summary: dict[str, object] = {
            "name": p.name,
            "life": p.life,
            "library_count": p.library_size,
        }

        hand_cards = p.hand
        p_summary["hand"] = [
            _record_name(c, source="hand card") if not isinstance(c, str) else str(c)
            for c in hand_cards
        ]

        if p.hand_count is not None:
            p_summary["hand_count"] = p.hand_count
        else:
            p_summary["hand_count"] = len(hand_cards)

        p_summary["battlefield"] = [_summarize_permanent(c) for c in p.battlefield]

        p_summary["graveyard"] = [
            _record_name(c, source="graveyard card")
            if not isinstance(c, str)
            else str(c)
            for c in p.graveyard
        ]

        if p.exile is not None:
            p_summary["exile"] = [
                _record_name(c, source="exile card")
                if not isinstance(c, str)
                else str(c)
                for c in p.exile
            ]

        if p.commanders is not None:
            p_summary["commanders"] = [
                _record_name(c, source="commander card")
                if not isinstance(c, str)
                else c
                for c in p.commanders
            ]

        if p.counters:
            p_summary["counters"] = p.counters

        players_summary.append(p_summary)

    summary: dict[str, object] = {
        "turn": snap.turn,
        "phase": snap.phase,
        "step": snap.step,
        "active_player": snap.active_player,
        "priority_player": snap.priority_player,
        "players": players_summary,
        "stack": [_summarize_stack_item(item) for item in snap.stack],
    }
    # Combat groups (may be absent in old exports)
    if snap.combat:
        summary["combat"] = [_summarize_combat_group(group) for group in snap.combat]
    return summary


def _find_snapshot_index(snapshots: Sequence[Snapshot], ts: str) -> int | None:
    """Find the index of the nearest snapshot at or before the given timestamp.

    Returns None if no snapshot exists at or before the timestamp (e.g. for
    play/draw decisions that happen before the first snapshot).
    """
    best: int | None = None
    for i, snap in enumerate(snapshots):
        snap_ts = snap.ts if snap.ts is not None else ""
        if snap_ts <= ts:
            best = i
        else:
            break
    return best


def _find_snapshot_index_by_seq(snapshots: Sequence[Snapshot], seq: int) -> int | None:
    """Find the index of the nearest snapshot at or before the given game seq.

    Used for v2 games where snapshots have seq but no ts.
    Returns None if no snapshot exists at or before the seq.
    """
    best: int | None = None
    for i, snap in enumerate(snapshots):
        snap_seq = snap.seq
        if snap_seq <= seq:
            best = i
        else:
            break
    return best


def _parse_choices_result(result_str: str | None) -> JsonObject:
    """Parse the result of a get_action_choices tool call."""
    if result_str is None:
        return {}
    try:
        parsed = json.loads(result_str)
    except json.JSONDecodeError:
        return {}
    assert isinstance(parsed, dict), (
        f"get_action_choices result must be a JSON object, got {parsed!r}"
    )
    return parsed


def _parse_action_result(result_str: str | None) -> JsonObject:
    """Parse the result of a choose_action tool call."""
    if result_str is None:
        return {}
    try:
        parsed = json.loads(result_str)
    except json.JSONDecodeError:
        return {}
    assert isinstance(parsed, dict), (
        f"choose_action result must be a JSON object, got {parsed!r}"
    )
    return parsed


def _is_failed_choose_action_result(result: JsonObject) -> bool:
    """Return True when choose_action did not resolve the pending action."""
    success = result.get("success")
    if success is False:
        return True
    return "error" in result or "error_code" in result


def _resolve_chosen_index(
    chosen_args: JsonObject, available_choices: list[object], action_result: JsonObject
) -> object | None:
    """Resolve chosen_index from choose_action args.

    Handles both new format (choice field) and old format (index/id/answer).
    Falls back to parsing the trailing integer from action_taken.
    """
    # New format: unified choice field (epoch 36+)
    if "choice" in chosen_args:
        choice = str(chosen_args["choice"]).strip()
        if choice.lower() in ("yes", "true"):
            return True
        if choice.lower() in ("no", "false"):
            return False
        try:
            return int(choice)
        except ValueError:
            # Treat as ID
            for ci, c in enumerate(available_choices):
                if isinstance(c, dict) and c.get("id") == choice:
                    return ci
            return None

    # Old format: separate index/id/answer fields (pre-epoch 36)
    has_id = "id" in chosen_args and chosen_args["id"]

    if "index" in chosen_args:
        # When both id and index are present, the bridge prefers id.
        if has_id:
            target_id = chosen_args["id"]
            for ci, c in enumerate(available_choices):
                if isinstance(c, dict) and c.get("id") == target_id:
                    return ci
            # id didn't match any choice; fall through to index
        chosen_index: object = chosen_args["index"]
        return chosen_index
    if "answer" in chosen_args:
        chosen_answer: object = chosen_args["answer"]
        return chosen_answer
    if "amount" in chosen_args:
        chosen_amount: object = chosen_args["amount"]
        return chosen_amount
    if has_id:
        target_id = chosen_args["id"]
        for ci, c in enumerate(available_choices):
            if isinstance(c, dict) and c.get("id") == target_id:
                return ci
    # Fallback: parse trailing integer from action_taken.
    # Handles selected_0, selected_target_1, selected_ability_0, etc.
    taken = action_result.get("action_taken")
    if taken is not None:
        assert isinstance(taken, str), (
            f"action_taken must be a string when present, got {taken!r}"
        )
    if isinstance(taken, str) and taken.startswith("selected"):
        try:
            return int(taken.rsplit("_", 1)[1])
        except (ValueError, IndexError):
            pass
    return None


# Messages where the player can always decline (pass/not attack/not block).
_PASS_ALLOWED_PREFIXES = (
    "Play ",
    "Choose spell or ability",
    "Choose ability",
    "Select attacker",
    "Select blocker",
)


def _is_forced(response_type: str, message: str, choices: list) -> bool:
    """Determine if a decision is truly forced (no meaningful choice).

    Boolean questions always have yes/no.
    Single-choice selects where the player can pass are not forced.
    """
    if response_type == "boolean":
        return False
    n = len(choices)
    if n == 0:
        return True
    if n == 1:
        return not message.startswith(_PASS_ALLOWED_PREFIXES)
    return False


def _build_decision(
    *,
    decisions: list[dict[str, object]],
    snap_idx: int | None,
    action_ts: str,
    action_seq: int = 0,
    player: str,
    game_state: dict[str, object],
    message: str,
    action_type: str,
    response_type: str,
    available_choices: list[object],
    chosen_index: object | None,
    chosen_args: JsonObject,
    action_result: JsonObject,
    reasoning: str,
    combat_phase: str,
    combat: list[object],
    already_attacking: list[object],
    incoming_attackers: list[object],
    subsequent: list[str],
) -> dict[str, object]:
    """Build a decision dict with the canonical field set."""
    d: dict[str, object] = {
        "decision_index": len(decisions),
        "snapshot_index": snap_idx if snap_idx is not None else 0,
        "action_ts": action_ts,
        "player": player,
        "turn": game_state.get("turn"),
        "phase": game_state.get("phase"),
        "message": message,
        "action_type": action_type,
        "response_type": response_type,
        "choices": available_choices,
        "choice_count": len(available_choices),
        "chosen": chosen_index,
        "chosen_args": chosen_args,
        "action_result": action_result,
        "reasoning": reasoning,
        "is_forced": _is_forced(response_type, message, available_choices),
        "game_state": game_state,
        "combat_phase": combat_phase,
        "combat": combat,
        "already_attacking": already_attacking,
        "incoming_attackers": incoming_attackers,
        "subsequent_actions": subsequent,
    }
    if action_seq:
        d["action_seq"] = action_seq
    return d


def _extract_decisions_v1(data: BuiltGameExport) -> list[dict[str, object]]:
    """Extract decisions from v1 format (harnessEpoch < 20).

    In v1, LLMs always call get_action_choices to get choices, then
    choose_action to respond. Decisions anchor on get_action_choices events.
    """
    snapshots = data.snapshots
    actions = data.actions
    llm_events = data.llm_events

    # Collect get_action_choices events with their indices
    choices_events: list[tuple[int, ToolCallEvent]] = []
    for i, event in enumerate(llm_events):
        if event.type == "tool_call" and event.tool == "get_action_choices":
            choices_events.append((i, event))

    decisions: list[dict[str, object]] = []

    for ce_idx, (event_idx, choices_event) in enumerate(choices_events):
        choices_result = _parse_choices_result(choices_event.result)
        if not choices_result.get("action_pending", True):
            continue

        choices_ts = choices_event.ts
        player = choices_event.player

        # Parse available choices
        available_choices_raw = choices_result.get("choices")
        if available_choices_raw is not None:
            available_choices = (
                available_choices_raw if isinstance(available_choices_raw, list) else []
            )
        else:
            available_choices = []
        response_type = choices_result.get("response_type")
        action_type = choices_result.get("action_type")
        message = choices_result.get("message")
        combat_phase = choices_result.get("combat_phase")
        if response_type is not None:
            assert isinstance(response_type, str), (
                f"response_type must be a string, got {response_type!r}"
            )
        else:
            response_type = ""
        if action_type is not None:
            assert isinstance(action_type, str), (
                f"action_type must be a string, got {action_type!r}"
            )
        else:
            action_type = ""
        if message is not None:
            assert isinstance(message, str), (
                f"message must be a string, got {message!r}"
            )
        else:
            message = ""
        if combat_phase is not None:
            assert isinstance(combat_phase, str), (
                f"combat_phase must be a string when present, got {combat_phase!r}"
            )
        else:
            combat_phase = ""

        combat_raw = choices_result.get("combat")
        combat = combat_raw if isinstance(combat_raw, list) else []

        already_attacking_raw = choices_result.get("already_attacking")
        already_attacking = (
            already_attacking_raw if isinstance(already_attacking_raw, list) else []
        )

        incoming_attackers_raw = choices_result.get("incoming_attackers")
        incoming_attackers = (
            incoming_attackers_raw if isinstance(incoming_attackers_raw, list) else []
        )

        # Look forward for the next llm_response and choose_action from same player
        reasoning = ""
        chosen_index = None
        chosen_args: JsonObject = {}
        action_result: JsonObject = {}
        action_ts = ""

        for j in range(event_idx + 1, min(event_idx + 20, len(llm_events))):
            ev = llm_events[j]
            if ev.player != player:
                continue

            if ev.type == "llm_response" and not reasoning:
                reasoning = ev.reasoning if ev.reasoning is not None else ""

            if ev.type == "tool_call" and ev.tool == "choose_action":
                chosen_args = ev.args
                action_result = _parse_action_result(ev.result)
                chosen_index = _resolve_chosen_index(
                    chosen_args, available_choices, action_result
                )
                if ev.ts is not None:
                    action_ts = ev.ts
                if _is_failed_choose_action_result(action_result):
                    continue
                break

            # If we hit another get_action_choices, stop
            if ev.type == "tool_call" and ev.tool == "get_action_choices":
                break

        # Find nearest snapshot (None if decision precedes all snapshots,
        # e.g. play/draw choice before hands are dealt)
        snap_idx = _find_snapshot_index(snapshots, choices_ts) if choices_ts else None
        game_state = (
            _summarize_snapshot(snapshots[snap_idx]) if snap_idx is not None else {}
        )

        # Collect subsequent game actions (between this decision and next)
        next_choices_ts: str | None = None
        if ce_idx + 1 < len(choices_events):
            next_choices_ts = choices_events[ce_idx + 1][1].ts

        subsequent: list[str] = []
        if action_ts:
            for a in actions:
                a_ts = a.ts
                if a_ts is not None:
                    assert isinstance(a_ts, str), (
                        f"action ts must be a string when present, got {a_ts!r}"
                    )
                compare_ts = action_ts if action_ts else choices_ts
                if a_ts is None or compare_ts is None or a_ts <= compare_ts:
                    continue
                if next_choices_ts and a_ts > next_choices_ts:
                    break
                message_raw = a.message
                if message_raw is not None:
                    assert isinstance(message_raw, str), (
                        f"action message must be a string when present, got {message_raw!r}"
                    )
                    subsequent.append(message_raw)
                if len(subsequent) >= 5:
                    break

        decisions.append(
            _build_decision(
                decisions=decisions,
                snap_idx=snap_idx,
                action_ts=action_ts,
                player=player,
                game_state=game_state,
                message=message,
                action_type=action_type,
                response_type=response_type,
                available_choices=available_choices,
                chosen_index=chosen_index,
                chosen_args=chosen_args,
                action_result=action_result,
                reasoning=reasoning,
                combat_phase=combat_phase,
                combat=combat,
                already_attacking=already_attacking,
                incoming_attackers=incoming_attackers,
                subsequent=subsequent,
            )
        )

    return decisions


def _is_decision_source(event: LlmEvent) -> bool:
    """Check if a tool_call event is a v2 decision source.

    A decision source is a pass_priority or get_action_choices tool_call
    whose result has action_pending=true.
    """
    if event.type != "tool_call":
        return False
    tool = event.tool
    if tool not in ("pass_priority", "get_action_choices"):
        return False
    result = _parse_choices_result(event.result)
    return bool(result.get("action_pending"))


def _extract_decisions_v2(data: BuiltGameExport) -> list[dict[str, object]]:
    """Extract decisions from v2 format (harnessEpoch >= 20).

    In v2, pass_priority returns choices inline when action_pending=true,
    so decisions anchor on pass_priority/get_action_choices events with
    action_pending=true instead of just get_action_choices.
    """
    snapshots = data.snapshots
    actions = data.actions
    llm_events = data.llm_events

    # Collect decision source events
    decision_sources: list[tuple[int, ToolCallEvent]] = []
    for i, event in enumerate(llm_events):
        # type check is redundant with _is_decision_source but needed for mypy narrowing
        if event.type == "tool_call" and _is_decision_source(event):
            decision_sources.append((i, event))

    decisions: list[dict[str, object]] = []

    for ds_idx, (event_idx, source_event) in enumerate(decision_sources):
        choices_result = _parse_choices_result(source_event.result)
        player = source_event.player

        available_choices_raw = choices_result.get("choices")
        if available_choices_raw is not None:
            available_choices = (
                available_choices_raw if isinstance(available_choices_raw, list) else []
            )
        else:
            available_choices = []
        response_type = choices_result.get("response_type")
        action_type = choices_result.get("action_type")
        message = choices_result.get("message")
        combat_phase = choices_result.get("combat_phase")
        if response_type is not None:
            assert isinstance(response_type, str), (
                f"response_type must be a string, got {response_type!r}"
            )
        else:
            response_type = ""
        if action_type is not None:
            assert isinstance(action_type, str), (
                f"action_type must be a string, got {action_type!r}"
            )
        else:
            action_type = ""
        if message is not None:
            assert isinstance(message, str), (
                f"message must be a string, got {message!r}"
            )
        else:
            message = ""
        if combat_phase is not None:
            assert isinstance(combat_phase, str), (
                f"combat_phase must be a string when present, got {combat_phase!r}"
            )
        else:
            combat_phase = ""

        combat_raw = choices_result.get("combat")
        combat = combat_raw if isinstance(combat_raw, list) else []

        already_attacking_raw = choices_result.get("already_attacking")
        already_attacking = (
            already_attacking_raw if isinstance(already_attacking_raw, list) else []
        )

        incoming_attackers_raw = choices_result.get("incoming_attackers")
        incoming_attackers = (
            incoming_attackers_raw if isinstance(incoming_attackers_raw, list) else []
        )

        # Look forward for llm_response and choose_action from same player
        reasoning = ""
        chosen_index = None
        chosen_args: JsonObject = {}
        action_result: JsonObject = {}
        action_ts = ""
        action_seq = 0

        for j in range(event_idx + 1, len(llm_events)):
            ev = llm_events[j]
            if ev.player != player:
                continue

            if ev.type == "llm_response" and not reasoning:
                reasoning = ev.reasoning if ev.reasoning is not None else ""

            if ev.type == "tool_call" and ev.tool == "choose_action":
                chosen_args = ev.args
                action_result = _parse_action_result(ev.result)
                chosen_index = _resolve_chosen_index(
                    chosen_args, available_choices, action_result
                )
                if ev.ts is not None:
                    action_ts = ev.ts
                game_seq_raw = ev.game_seq if ev.game_seq is not None else action_seq
                if isinstance(game_seq_raw, int) and not isinstance(game_seq_raw, bool):
                    action_seq = game_seq_raw
                if _is_failed_choose_action_result(action_result):
                    continue
                break

            # Stop at next decision source for this player
            if _is_decision_source(ev):
                break

        # Use seq-based snapshot lookup for v2 (snapshots have seq, not ts).
        # Fall back to timestamp if gameSeq is missing (e.g. discard-to-hand-size
        # events from older harness versions that didn't emit gameSeq).
        choices_seq_raw = source_event.game_seq
        choices_seq = (
            choices_seq_raw
            if isinstance(choices_seq_raw, int)
            and not isinstance(choices_seq_raw, bool)
            else 0
        )
        if choices_seq:
            snap_idx = _find_snapshot_index_by_seq(snapshots, choices_seq)
        else:
            choices_ts = source_event.ts
            snap_idx = (
                _find_snapshot_index(snapshots, choices_ts) if choices_ts else None
            )
        game_state = (
            _summarize_snapshot(snapshots[snap_idx]) if snap_idx is not None else {}
        )

        # Collect subsequent game actions using seq
        if not action_seq:
            action_seq = choices_seq

        next_choices_seq = 0
        if ds_idx + 1 < len(decision_sources):
            next_game_seq_raw = decision_sources[ds_idx + 1][1].game_seq
            if isinstance(next_game_seq_raw, int) and not isinstance(
                next_game_seq_raw, bool
            ):
                next_choices_seq = next_game_seq_raw

        subsequent: list[str] = []
        for a in actions:
            a_seq = a.seq
            if a_seq <= action_seq:
                continue
            if next_choices_seq and a_seq > next_choices_seq:
                break
            message_raw = a.message
            if isinstance(message_raw, str) and message_raw:
                subsequent.append(message_raw)
            if len(subsequent) >= 5:
                break

        decisions.append(
            _build_decision(
                decisions=decisions,
                snap_idx=snap_idx,
                action_ts=action_ts,
                action_seq=action_seq,
                player=player,
                game_state=game_state,
                message=message,
                action_type=action_type,
                response_type=response_type,
                available_choices=available_choices,
                chosen_index=chosen_index,
                chosen_args=chosen_args,
                action_result=action_result,
                reasoning=reasoning,
                combat_phase=combat_phase,
                combat=combat,
                already_attacking=already_attacking,
                incoming_attackers=incoming_attackers,
                subsequent=subsequent,
            )
        )

    return decisions


def extract_decisions(gz_path: str) -> list[Decision]:
    """Extract decision points from a game export file.

    Returns canonical Decision dataclass instances from the export's
    pre-built 'decisions' field.  All current exports have this field;
    the legacy extraction helpers (_extract_decisions_v1/v2) are retained
    for their independent test coverage but are no longer called here.
    """
    data = load_game_for_annotation(gz_path)

    assert data.decisions is not None, (
        f"Game export {gz_path} missing decisions[] field — "
        "all exports must have pre-built decisions"
    )
    return list(data.decisions)


_CAST_PROMPT_PREFIXES = (
    "Play spells and abilities",
    "Play instants and activated abilities",
)


def _find_spell_cancelled_events(
    llm_events: Sequence[LlmEvent],
) -> list[tuple[str, str]]:
    """Find (player, timestamp) pairs for [System] Spell cancelled messages.

    These messages can appear in any tool result (get_action_choices,
    choose_action, pass_priority) — not just get_action_choices.

    The timestamp is backdated to the previous tool_call for the same player,
    since the cancellation happens during a blocking call (e.g. pass_priority)
    but only surfaces in the result.
    """
    # Track the previous tool_call timestamp per player for backdating
    last_ts: dict[str, str] = {}
    cancelled: list[tuple[str, str]] = []
    for ev in llm_events:
        if ev.type != "tool_call":
            continue
        player = ev.player
        result_str = ev.result
        if "[System] Spell cancelled" not in result_str:
            if ev.ts is not None:
                last_ts[player] = ev.ts
            continue
        try:
            result = json.loads(result_str)
        except (json.JSONDecodeError, TypeError):
            if ev.ts is not None:
                last_ts[player] = ev.ts
            continue
        if not isinstance(result, dict):
            continue
        recent_chat = result.get("recent_chat")
        if not isinstance(recent_chat, list):
            recent_chat = []
        for msg in recent_chat:
            if "[System] Spell cancelled" in str(msg):
                # Use the previous event's timestamp (when the cast was attempted)
                if player in last_ts:
                    ts = last_ts[player]
                elif ev.ts is not None:
                    ts = ev.ts
                else:
                    break
                cancelled.append((player, ts))
                break
        if ev.ts is not None:
            last_ts[player] = ev.ts
    return cancelled


def _mark_rolled_back_casts(
    decisions: list[dict[str, object]], cancelled_events: list[tuple[str, str]]
) -> None:
    """Post-process decisions to mark rolled-back cast sequences.

    When XMage can't complete mana payment for a spell, it silently rolls back
    the cast. The MCP layer detects this and adds a "[System] Spell cancelled"
    message to recent_chat. We use that signal to walk backwards and mark:
    - Intermediate decisions (cost choice, mana taps) as rolled_back=True
    - The initiating "Play spells" decision as cast_rolled_back=True
    """
    # Process in timestamp order so sequential rollbacks for the same player
    # don't collide (the "already marked" check stops the backward walk).
    for player, cancel_ts in sorted(cancelled_events, key=lambda x: x[1]):
        for j in range(len(decisions) - 1, -1, -1):
            d = decisions[j]
            if d["player"] != player:
                continue
            # Skip decisions after the cancel event
            action_ts_raw = d["action_ts"]
            assert isinstance(action_ts_raw, str), (
                f"action_ts must be a string, got {action_ts_raw!r}"
            )
            if action_ts_raw > cancel_ts:
                continue
            # Already handled by a previous cancel event
            if d.get("rolled_back") or d.get("cast_rolled_back"):
                break
            msg = d["message"]
            assert isinstance(msg, str), f"message must be a string, got {msg!r}"
            if msg.startswith(_CAST_PROMPT_PREFIXES):
                d["cast_rolled_back"] = True
                break
            d["rolled_back"] = True


def main(gz_path: str) -> None:
    decisions = extract_decisions(gz_path)
    json.dump(
        list(decisions),
        sys.stdout,
        indent=2,
        default=json_default,
    )
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <game.json.gz>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
