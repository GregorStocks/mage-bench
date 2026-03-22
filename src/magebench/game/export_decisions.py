"""Decision construction helpers for game export construction."""

import json

from magebench.game.game_export_types import (
    Choice,
    Decision,
    MultiAmountItem,
    PilotContext,
)


def _parse_json(s: str | None) -> dict:
    """Parse a JSON string, returning {} on failure."""
    if s is None:
        return {}
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError:
        return {}
    assert isinstance(parsed, dict), (
        f"tool result must be a JSON object, got {parsed!r}"
    )
    return parsed


def _is_decision_source(event: dict) -> bool:
    """Check if a tool_call event is a decision source.

    A decision source is a pass_priority, get_action_choices, or choose_action
    tool_call whose result has action_pending=true. Including choose_action
    means that each action within a priority window becomes its own decision
    rather than being bundled into the initial pass_priority decision.
    """
    if event.get("type") != "tool_call":
        return False
    tool = event.get("tool")
    if tool not in ("pass_priority", "get_action_choices", "choose_action"):
        return False
    result = _parse_json(event.get("result"))
    return bool(result.get("action_pending"))


def _is_v1_decision_source(event: dict) -> bool:
    """Check if a tool_call event is a v1 decision source.

    In v1 (harness_epoch < 20), decisions anchor on get_action_choices events
    with action_pending=true.
    """
    if event.get("type") != "tool_call":
        return False
    if event.get("tool") != "get_action_choices":
        return False
    result = _parse_json(event.get("result"))
    action_pending = result.get("action_pending", True)
    assert isinstance(action_pending, bool), (
        f"get_action_choices action_pending must be a bool, got {action_pending!r}"
    )
    return action_pending


def _is_failed_choose_action_result(result: dict) -> bool:
    """Return True when choose_action did not resolve the pending action."""
    success = result.get("success")
    if success is False:
        return True
    return "error" in result or "error_code" in result


def _has_followup_choose_action(
    llm_events: list[dict], event_idx: int, player: str, *, uses_v2_sources: bool
) -> bool:
    """Return True if the source is followed by another choose_action response."""
    for j in range(event_idx + 1, len(llm_events)):
        ev = llm_events[j]
        if ev.get("player") != player:
            continue
        if ev.get("type") == "tool_call" and ev.get("tool") == "choose_action":
            return True
        if uses_v2_sources and _is_decision_source(ev):
            return False
        if not uses_v2_sources and _is_v1_decision_source(ev):
            return False
    return False


def _follows_failed_choose_action_retry(
    llm_events: list[dict],
    decision_sources: list[tuple[int, dict]],
    source_idx: int,
    *,
    uses_v2_sources: bool,
) -> bool:
    """Return True when a choose_action source only exists after a failed retry."""
    event_idx, source_event = decision_sources[source_idx]
    if source_event.get("tool") != "choose_action":
        return False

    player = source_event["player"]
    scan_start = 0
    for prev_idx in range(source_idx - 1, -1, -1):
        prev_event_idx, prev_source = decision_sources[prev_idx]
        if prev_source.get("player") == player:
            scan_start = prev_event_idx + 1
            break

    for j in range(scan_start, event_idx):
        ev = llm_events[j]
        if ev.get("player") != player:
            continue
        if (
            ev.get("type") == "tool_call"
            and ev.get("tool") == "choose_action"
            and _is_failed_choose_action_result(_parse_json(ev.get("result")))
        ):
            return True
        if uses_v2_sources and _is_decision_source(ev):
            break
        if not uses_v2_sources and _is_v1_decision_source(ev):
            break
    return False


def _collect_decision_sources(
    llm_events: list[dict], harness_epoch: int
) -> list[tuple[int, dict]]:
    """Collect decision sources, dropping synthetic blanks after failed retries."""
    is_v2 = harness_epoch >= 20
    candidate_sources: list[tuple[int, dict]] = []
    for i, event in enumerate(llm_events):
        if is_v2:
            if _is_decision_source(event):
                candidate_sources.append((i, event))
        else:
            if _is_v1_decision_source(event):
                candidate_sources.append((i, event))

    decision_sources: list[tuple[int, dict]] = []
    for source_idx, (event_idx, source_event) in enumerate(candidate_sources):
        if source_event.get("tool") != "choose_action":
            decision_sources.append((event_idx, source_event))
            continue
        player = source_event["player"]
        if _follows_failed_choose_action_retry(
            llm_events,
            candidate_sources,
            source_idx,
            uses_v2_sources=is_v2,
        ) and not _has_followup_choose_action(
            llm_events,
            event_idx,
            player,
            uses_v2_sources=is_v2,
        ):
            continue
        decision_sources.append((event_idx, source_event))
    return decision_sources


def _resolve_chosen_index(
    chosen_args: dict, available_choices: list, action_result: dict
) -> object | None:
    """Resolve chosen_index from choose_action args.

    Handles both new format (choice field) and old format (index/id/answer).
    Falls back to parsing the trailing integer from action_taken.
    """
    if "choice" in chosen_args:
        choice = str(chosen_args["choice"]).strip()
        if choice.lower() in ("yes", "true"):
            return True
        if choice.lower() in ("no", "false"):
            return False
        try:
            return int(choice)
        except ValueError:
            for ci, current_choice in enumerate(available_choices):
                if (
                    isinstance(current_choice, dict)
                    and current_choice.get("id") == choice
                ):
                    return ci
            return None

    has_id = "id" in chosen_args and chosen_args["id"]

    if "index" in chosen_args:
        if has_id:
            target_id = chosen_args["id"]
            for ci, current_choice in enumerate(available_choices):
                if (
                    isinstance(current_choice, dict)
                    and current_choice.get("id") == target_id
                ):
                    return ci
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
        for ci, current_choice in enumerate(available_choices):
            if (
                isinstance(current_choice, dict)
                and current_choice.get("id") == target_id
            ):
                return ci
    taken = action_result.get("action_taken")
    if taken and taken.startswith("selected"):
        try:
            return int(taken.rsplit("_", 1)[1])
        except (ValueError, IndexError):
            pass
    return None


def _find_snapshot_index_by_seq(snapshots: list[dict], seq: int) -> int | None:
    """Find the index of the nearest snapshot at or before the given game seq."""
    best: int | None = None
    for i, snap in enumerate(snapshots):
        if snap.get("seq", 0) <= seq:
            best = i
        else:
            break
    return best


def _find_snapshot_index_by_ts(snapshots: list[dict], ts: str) -> int | None:
    """Find the index of the nearest snapshot at or before the given timestamp."""
    best: int | None = None
    for i, snap in enumerate(snapshots):
        if (snap["ts"] if "ts" in snap else "") <= ts:
            best = i
        else:
            break
    return best


def _extract_pilot_context(choices_result: dict) -> PilotContext | None:
    """Extract pilot-specific overlay data from a tool result."""
    ctx: dict[str, object] = {}
    if "untapped_lands" in choices_result:
        ctx["untapped_lands"] = choices_result["untapped_lands"]
    if "land_drops_used" in choices_result:
        ctx["land_drops_used"] = choices_result["land_drops_used"]
    if "combat_phase" in choices_result:
        ctx["combat_phase"] = choices_result["combat_phase"] or None
    if "already_attacking" in choices_result:
        ctx["already_attacking"] = choices_result["already_attacking"]
    if "incoming_attackers" in choices_result:
        ctx["incoming_attackers"] = choices_result["incoming_attackers"]
    board = choices_result.get("board")
    if isinstance(board, dict):
        board = board.get("players")
    playable_ids: list[str] = []
    if isinstance(board, list):
        for player in board:
            if isinstance(player, dict):
                hand = player.get("hand")
                if hand is not None:
                    for card in hand:
                        if isinstance(card, dict) and card.get("playable"):
                            card_id = card.get("id")
                            if card_id:
                                playable_ids.append(card_id)
    if playable_ids:
        ctx["playable_cards"] = playable_ids
    if not ctx:
        return None
    return PilotContext.from_mapping(ctx)


_CAST_PROMPT_PREFIXES = (
    "Play spells and abilities",
    "Play instants and activated abilities",
)


def _find_spell_cancelled_seqs(llm_events: list[dict]) -> list[tuple[str, int]]:
    """Find (player, event_index) pairs for [System] Spell cancelled messages.

    Returns the index of the tool_call event before the one containing the
    cancel message.
    """
    last_idx: dict[str, int] = {}
    cancelled: list[tuple[str, int]] = []
    for i, ev in enumerate(llm_events):
        if ev.get("type") != "tool_call":
            continue
        player = ev["player"]
        result_str = ev["result"]
        if "[System] Spell cancelled" not in result_str:
            last_idx[player] = i
            continue
        result = _parse_json(result_str)
        recent_chat = result.get("recent_chat")
        if recent_chat is not None:
            for msg in recent_chat:
                if "[System] Spell cancelled" in str(msg):
                    cancelled.append((player, last_idx.get(player, i)))
                    break
        last_idx[player] = i
    return cancelled


def _mark_rolled_back_casts(
    decisions: list[Decision], cancelled: list[tuple[str, int]]
) -> None:
    """Mark rolled-back cast sequences on canonical decisions."""
    for player, cancel_idx in sorted(cancelled, key=lambda item: item[1]):
        for j in range(len(decisions) - 1, -1, -1):
            decision = decisions[j]
            if decision.player != player:
                continue
            indices = decision.llm_event_indices
            if not indices:
                continue
            if indices[0] > cancel_idx:
                continue
            if decision.cast_rolled_back:
                break
            msg = decision.message
            if msg and msg.startswith(_CAST_PROMPT_PREFIXES):
                decision.cast_rolled_back = True
                break


_PASS_ALLOWED_PREFIXES = (
    "Play ",
    "Choose spell or ability",
    "Choose ability",
    "Select attacker",
    "Select blocker",
)


def _is_forced(response_type: str | None, message: str | None, choices: list) -> bool:
    """Determine if a decision is truly forced (no meaningful choice)."""
    if response_type == "boolean":
        return False
    num_choices = len(choices)
    if num_choices == 0:
        return True
    if num_choices == 1:
        return not (message and message.startswith(_PASS_ALLOWED_PREFIXES))
    return False


def build_decisions(
    snapshots: list[dict],
    actions: list[dict],
    llm_events: list[dict],
    harness_epoch: int,
) -> list[Decision]:
    """Build canonical decision records from export data."""
    is_v2 = harness_epoch >= 20
    decision_sources = _collect_decision_sources(llm_events, harness_epoch)
    decisions: list[Decision] = []

    for ds_idx, (event_idx, source_event) in enumerate(decision_sources):
        choices_result = _parse_json(source_event.get("result"))
        player = source_event["player"]

        available_choices = choices_result.get("choices")
        if available_choices is None:
            available_choices = []
        response_type = choices_result.get("response_type")
        action_type = choices_result.get("action_type")
        message = choices_result.get("message")
        if response_type is None:
            response_type = ""
        if action_type is None:
            action_type = ""
        if message is None:
            message = ""

        llm_event_indices: list[int] = [event_idx]
        chosen_index = None
        chosen_args: dict = {}
        action_result: dict = {}
        action_seq = source_event.get("game_seq") or 0

        for j in range(event_idx + 1, len(llm_events)):
            ev = llm_events[j]
            if ev.get("player") != player:
                continue

            if ev.get("type") == "llm_response":
                llm_event_indices.append(j)

            if ev.get("type") == "tool_call" and ev.get("tool") == "choose_action":
                llm_event_indices.append(j)
                assert "args" in ev, f"choose_action event missing args: {ev!r}"
                raw_args = ev["args"]
                assert isinstance(raw_args, dict), (
                    f"choose_action args must be an object, got {raw_args!r}"
                )
                chosen_args = raw_args
                action_result = _parse_json(ev.get("result"))
                chosen_index = _resolve_chosen_index(
                    chosen_args, available_choices, action_result
                )
                game_seq_raw = ev.get("game_seq", action_seq)
                if isinstance(game_seq_raw, int) and not isinstance(game_seq_raw, bool):
                    action_seq = game_seq_raw
                if _is_failed_choose_action_result(action_result):
                    continue
                break

            if is_v2 and _is_decision_source(ev):
                break
            if not is_v2 and _is_v1_decision_source(ev):
                break

        choices_seq = source_event.get("game_seq") or 0
        if choices_seq:
            snap_idx = _find_snapshot_index_by_seq(snapshots, choices_seq)
        else:
            choices_ts = source_event.get("ts")
            snap_idx = (
                _find_snapshot_index_by_ts(snapshots, choices_ts)
                if choices_ts
                else None
            )

        snap = snapshots[snap_idx] if snap_idx is not None else {}
        snap_idx_val = snap_idx if snap_idx is not None else 0

        next_choices_seq = 0
        if ds_idx + 1 < len(decision_sources):
            next_choices_seq = decision_sources[ds_idx + 1][1].get("game_seq", 0)

        subsequent: list[str] = []
        for action in actions:
            action_seq_value = action.get("seq", 0)
            if action_seq_value <= action_seq:
                continue
            if next_choices_seq and action_seq_value > next_choices_seq:
                break
            if action.get("message"):
                subsequent.append(action["message"])
            if len(subsequent) >= 5:
                break

        typed_choices = Choice.coerce_list(available_choices)

        decision = Decision(
            index=len(decisions),
            snapshot_index=snap_idx_val,
            player=player,
            turn=snap.get("turn", 0),
            phase=snap.get("phase"),
            step=snap.get("step"),
            action_type=action_type,
            response_type=response_type,
            message=message,
            choices=typed_choices,
            choice_count=len(typed_choices),
            is_forced=_is_forced(response_type, message, typed_choices),
            chosen=chosen_index,
            chosen_args=chosen_args,
            action_result=action_result,
            llm_event_indices=llm_event_indices,
            subsequent_actions=subsequent,
            action_seq=action_seq,
        )

        pilot_ctx = _extract_pilot_context(choices_result)
        if pilot_ctx:
            decision.pilot_context = pilot_ctx

        multi_items = choices_result.get("items")
        if multi_items:
            decision.items = MultiAmountItem.coerce_list(multi_items)
            if "total_min" in choices_result:
                decision.total_min = choices_result["total_min"]
            if "total_max" in choices_result:
                decision.total_max = choices_result["total_max"]

        decisions.append(decision)

    cancelled = _find_spell_cancelled_seqs(llm_events)
    if cancelled:
        _mark_rolled_back_casts(decisions, cancelled)

    return decisions
