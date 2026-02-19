#!/usr/bin/env python3
"""Extract LLM decision points from a .json.gz game export.

For each meaningful decision (get_action_choices -> llm_response -> choose_action),
outputs the game state, available choices, what was chosen, LLM reasoning, and
what happened next. Designed to give Claude Code structured data for blunder analysis.
"""

import gzip
import json
import sys


def _summarize_permanent(c: dict) -> str | dict:
    """Summarize a battlefield permanent. Returns just the name if nothing
    interesting, or a dict with extra info when tapped/counters/sick."""
    if not isinstance(c, dict):
        return str(c)
    name = c.get("name", "?")
    extras: dict = {}
    if c.get("tapped"):
        extras["tapped"] = True
    if c.get("summoning_sick"):
        extras["summoning_sick"] = True
    if c.get("counters"):
        extras["counters"] = c["counters"]
    if c.get("token"):
        extras["token"] = True
    if c.get("face_down"):
        extras["face_down"] = True
    if c.get("copy"):
        extras["copy"] = True
    if c.get("original_card"):
        extras["original_card"] = c["original_card"]
    if c.get("rules"):
        extras["rules"] = c["rules"]
    if extras:
        return {"name": name, **extras}
    return name


def _summarize_snapshot(snap: dict) -> dict:
    """Summarize a snapshot for decision context."""
    summary = {
        "turn": snap.get("turn"),
        "phase": snap.get("phase"),
        "step": snap.get("step"),
        "active_player": snap.get("active_player"),
        "priority_player": snap.get("priority_player"),
        "players": [
            {
                "name": p["name"],
                "life": p.get("life"),
                "hand": [
                    c.get("name", "?") if isinstance(c, dict) else str(c)
                    for c in p.get("hand", [])
                ],
                "hand_count": p.get("hand_count", len(p.get("hand", []))),
                "battlefield": [
                    _summarize_permanent(c) for c in p.get("battlefield", [])
                ],
                "graveyard": [
                    c.get("name", "?") if isinstance(c, dict) else str(c)
                    for c in p.get("graveyard", [])
                ],
                "exile": [
                    c.get("name", "?") if isinstance(c, dict) else str(c)
                    for c in p.get("exile", [])
                ],
                "commanders": [
                    c.get("name", "?") if isinstance(c, dict) else c
                    for c in p.get("commanders", [])
                ],
            }
            for p in snap.get("players", [])
        ],
        "stack": [
            item.get("name", "?") if isinstance(item, dict) else str(item)
            for item in snap.get("stack", [])
        ],
    }
    # Combat groups (may be absent in old exports)
    combat = snap.get("combat")
    if combat:
        summary["combat"] = combat
    return summary


def _find_snapshot_index(snapshots: list[dict], ts: str) -> int | None:
    """Find the index of the nearest snapshot at or before the given timestamp.

    Returns None if no snapshot exists at or before the timestamp (e.g. for
    play/draw decisions that happen before the first snapshot).
    """
    best: int | None = None
    for i, snap in enumerate(snapshots):
        snap_ts = snap.get("ts", "")
        if snap_ts <= ts:
            best = i
        else:
            break
    return best


def _parse_choices_result(result_str: str) -> dict:
    """Parse the result of a get_action_choices tool call."""
    try:
        return json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return {}


def _parse_action_result(result_str: str) -> dict:
    """Parse the result of a choose_action tool call."""
    try:
        return json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return {}


def extract_decisions(gz_path: str) -> list[dict]:
    """Extract decision points from a game gz file."""
    with gzip.open(gz_path, "rt") as f:
        data = json.load(f)

    snapshots = data.get("snapshots", [])
    actions = data.get("actions", [])
    llm_events = data.get("llmEvents", [])

    # Collect get_action_choices events with their indices
    choices_events: list[tuple[int, dict]] = []
    for i, event in enumerate(llm_events):
        if (
            event.get("type") == "tool_call"
            and event.get("tool") == "get_action_choices"
        ):
            choices_events.append((i, event))

    decisions: list[dict] = []

    for ce_idx, (event_idx, choices_event) in enumerate(choices_events):
        choices_result = _parse_choices_result(choices_event.get("result", ""))
        if not choices_result.get("action_pending", True):
            continue

        choices_ts = choices_event.get("ts", "")
        player = choices_event.get("player", "")

        # Parse available choices
        available_choices = choices_result.get("choices", [])
        choice_count = len(available_choices)
        response_type = choices_result.get("response_type", "")
        action_type = choices_result.get("action_type", "")
        message = choices_result.get("message", "")
        combat_phase = choices_result.get("combat_phase", "")
        combat = choices_result.get("combat", [])
        already_attacking = choices_result.get("already_attacking", [])
        incoming_attackers = choices_result.get("incoming_attackers", [])

        # Look forward for the next llm_response and choose_action from same player
        reasoning = ""
        chosen_index = None
        chosen_args: dict = {}
        action_result: dict = {}
        action_ts = ""

        for j in range(event_idx + 1, min(event_idx + 20, len(llm_events))):
            ev = llm_events[j]
            if ev.get("player") != player:
                continue

            if ev.get("type") == "llm_response" and not reasoning:
                reasoning = ev.get("reasoning", "")

            if ev.get("type") == "tool_call" and ev.get("tool") == "choose_action":
                chosen_args = ev.get("args", {})
                if "index" in chosen_args:
                    chosen_index = chosen_args["index"]
                elif "answer" in chosen_args:
                    chosen_index = chosen_args["answer"]
                elif "amount" in chosen_args:
                    chosen_index = chosen_args["amount"]
                action_result = _parse_action_result(ev.get("result", ""))
                action_ts = ev.get("ts", "")
                break

            # If we hit another get_action_choices, stop
            if ev.get("type") == "tool_call" and ev.get("tool") == "get_action_choices":
                break

        # Find nearest snapshot (None if decision precedes all snapshots,
        # e.g. play/draw choice before hands are dealt)
        snap_idx = _find_snapshot_index(snapshots, choices_ts)
        game_state = (
            _summarize_snapshot(snapshots[snap_idx]) if snap_idx is not None else {}
        )

        # Collect subsequent game actions (between this decision and next)
        next_choices_ts = ""
        if ce_idx + 1 < len(choices_events):
            next_choices_ts = choices_events[ce_idx + 1][1].get("ts", "")

        subsequent: list[str] = []
        if action_ts:
            for a in actions:
                a_ts = a.get("ts", "")
                if a_ts <= (action_ts or choices_ts):
                    continue
                if next_choices_ts and a_ts > next_choices_ts:
                    break
                subsequent.append(a.get("message", ""))
                if len(subsequent) >= 5:
                    break

        decisions.append(
            {
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
                "choice_count": choice_count,
                "chosen": chosen_index,
                "chosen_args": chosen_args,
                "action_result": action_result,
                "reasoning": reasoning,
                "is_forced": choice_count <= 1,
                "game_state": game_state,
                "combat_phase": combat_phase,
                "combat": combat,
                "already_attacking": already_attacking,
                "incoming_attackers": incoming_attackers,
                "subsequent_actions": subsequent,
            }
        )

    # Detect rolled-back casts from [System] Spell cancelled messages
    # in ANY tool result (get_action_choices, choose_action, pass_priority)
    cancelled = _find_spell_cancelled_events(llm_events)
    _mark_rolled_back_casts(decisions, cancelled)

    return decisions


_CAST_PROMPT_PREFIXES = (
    "Play spells and abilities",
    "Play instants and activated abilities",
)


def _find_spell_cancelled_events(llm_events: list[dict]) -> list[tuple[str, str]]:
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
        if ev.get("type") != "tool_call":
            continue
        player = ev.get("player", "")
        result_str = ev.get("result", "")
        if "[System] Spell cancelled" not in result_str:
            last_ts[player] = ev.get("ts", "")
            continue
        try:
            result = json.loads(result_str)
        except (json.JSONDecodeError, TypeError):
            last_ts[player] = ev.get("ts", "")
            continue
        for msg in result.get("recent_chat", []):
            if "[System] Spell cancelled" in str(msg):
                # Use the previous event's timestamp (when the cast was attempted)
                ts = last_ts.get(player, ev.get("ts", ""))
                cancelled.append((player, ts))
                break
        last_ts[player] = ev.get("ts", "")
    return cancelled


def _mark_rolled_back_casts(
    decisions: list[dict], cancelled_events: list[tuple[str, str]]
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
            if d.get("action_ts", "") > cancel_ts:
                continue
            # Already handled by a previous cancel event
            if d.get("rolled_back") or d.get("cast_rolled_back"):
                break
            msg = d.get("message", "")
            if msg.startswith(_CAST_PROMPT_PREFIXES):
                d["cast_rolled_back"] = True
                break
            d["rolled_back"] = True


def main(gz_path: str) -> None:
    decisions = extract_decisions(gz_path)
    json.dump(decisions, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <game.json.gz>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
