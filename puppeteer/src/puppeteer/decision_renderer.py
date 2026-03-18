"""Shared decision renderer for pilot and blunder annotator.

Renders a canonical decision (from the export or built live from MCP tool
results) into structured text for LLM consumption.

Both the pilot (at game time) and annotator (at analysis time) call
render_decision() with the same decision format but different oracle_texts
sources:
  - Pilot: rules extracted from bridge board payload
  - Annotator: fetched from Scryfall cache
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from schemas.game_export_types import (
    Choice,
    Decision,
    MultiAmountItem,
    PilotContext,
    decision_support_get,
    decision_support_has,
    export_record_field,
)

BASIC_LAND_NAMES = frozenset(
    [
        "Plains",
        "Island",
        "Swamp",
        "Mountain",
        "Forest",
        "Wastes",
        "Snow-Covered Plains",
        "Snow-Covered Island",
        "Snow-Covered Swamp",
        "Snow-Covered Mountain",
        "Snow-Covered Forest",
    ]
)

DecisionLike = Decision | dict[str, object]


def _decision_value(decision: DecisionLike, key: str) -> object:
    if isinstance(decision, Decision):
        return getattr(decision, key)
    return decision[key]


def _decision_optional(decision: DecisionLike, key: str) -> object:
    if isinstance(decision, Decision):
        return getattr(decision, key)
    return decision.get(key)


def _record_field(record: object, field: str) -> object | None:
    return export_record_field(record, field)


def _record_name(record: object, *, source: str) -> str:
    if isinstance(record, dict):
        name = record["name"]
        assert isinstance(name, str), f"{source} name must be a string, got {name!r}"
        return name
    name = _record_field(record, "name")
    assert isinstance(name, str), f"{source} name must be a string, got {name!r}"
    return name


def render_decision(
    decision: DecisionLike,
    snapshot: dict,
    oracle_texts: dict[str, dict] | None = None,
    *,
    deciding_player: str | None = None,
    include_card_reference: bool = False,
    include_chosen: bool = False,
    prior_context: str = "",
    current_turn_actions: str = "",
) -> str:
    """Render a canonical decision into structured text.

    Args:
        decision: Canonical decision dict (from export or built live).
        snapshot: The referenced snapshot (from export snapshots[] or MCP board).
        oracle_texts: Card name -> oracle fields dict. Optional.
        deciding_player: Who's deciding (for hand redaction). When set,
            opponent hands show only hand_size/hand_count.
        include_card_reference: Prepend a ## Card Reference section.
        include_chosen: Append chosen action (for annotator).
        prior_context: Pre-formatted prior context string (annotator-specific).
            Should already include its own ## heading.
        current_turn_actions: Pre-formatted current turn actions string.
            Should already include its own ## heading.

    Returns:
        Rendered text suitable for LLM consumption.
    """
    parts: list[str] = []

    # Card reference section (with heading)
    if include_card_reference and oracle_texts:
        card_ref = _render_card_reference(decision, snapshot, oracle_texts)
        if card_ref:
            parts.append(card_ref)

    # Prior context (annotator passes pre-formatted string with ## heading)
    if prior_context:
        parts.append(prior_context)

    # Current turn actions (pre-formatted with ## heading)
    if current_turn_actions:
        parts.append(current_turn_actions)

    # Main decision block (with heading)
    decision_block = _render_decision_block(decision, snapshot, deciding_player)
    decision_parts = [f"## Decision\n\n{decision_block}"]

    # Chosen action (for annotator)
    if include_chosen:
        chosen_block = _render_chosen_block(decision, snapshot)
        if chosen_block:
            decision_parts.append(chosen_block)

    parts.append("\n\n".join(decision_parts))

    return "\n\n".join(parts)


def _render_decision_block(
    decision: DecisionLike,
    snapshot: dict,
    deciding_player: str | None,
) -> str:
    """Render the core decision: board state, stack, choices."""
    turn = _decision_value(decision, "turn")
    assert isinstance(turn, int), f"decision turn must be an int, got {turn!r}"
    phase_value = _decision_value(decision, "phase")
    if not phase_value:
        message_value = _decision_value(decision, "message")
        assert turn in (0, 1), f"decision has empty phase on turn {turn}: {message_value}"
    phase = phase_value or "PREGAME"
    player = _decision_value(decision, "player")
    message = _decision_value(decision, "message")
    assert isinstance(player, str), f"decision player must be a string, got {player!r}"
    assert isinstance(message, str), f"decision message must be a string, got {message!r}"

    # Header
    lines: list[str] = [
        (
            f"[Decision {_decision_value(decision, 'index')}, "
            f"snapshot={_decision_value(decision, 'snapshotIndex')}] "
            f"Turn {turn} {phase} - {player}"
        )
    ]
    pilot_ctx: dict[str, object] | PilotContext = {}
    raw_pilot_ctx = _decision_optional(decision, "pilotContext")
    if raw_pilot_ctx is not None:
        assert isinstance(raw_pilot_ctx, (dict, PilotContext)), (
            f"pilotContext must be an object when present, got {raw_pilot_ctx!r}"
        )
        pilot_ctx = raw_pilot_ctx

    # Board state from snapshot
    board_line = _render_board(snapshot, deciding_player)
    lines.append(f"  Board: {board_line}")

    # Stack
    stack = snapshot.get("stack")
    if stack:
        stack_parts = _render_stack(stack)
        lines.append(f"  Stack: [{', '.join(stack_parts)}]")

    # Combat
    combat_groups = snapshot.get("combat")
    if combat_groups:
        combat_line = _render_combat(combat_groups)
        lines.append(f"  Combat: {combat_line}")

    combat_phase = decision_support_get(pilot_ctx, "combatPhase")
    assert combat_phase is None or isinstance(combat_phase, str), (
        f"combatPhase must be a string when present, got {combat_phase!r}"
    )
    if combat_phase:
        lines.append(f"  Combat Phase: {combat_phase}")

    # Pilot context overlay
    incoming_attackers = decision_support_get(pilot_ctx, "incomingAttackers")
    assert incoming_attackers is None or isinstance(incoming_attackers, list), (
        f"incomingAttackers must be a list when present, got {incoming_attackers!r}"
    )
    if _is_declare_blockers_phase(combat_phase) and incoming_attackers:
        lines.append(f"  Incoming Attackers: {_render_incoming_attackers(incoming_attackers)}")

    if decision_support_has(pilot_ctx, "untappedLands") or decision_support_has(pilot_ctx, "landDropsUsed"):
        ctx_parts: list[str] = []
        if decision_support_has(pilot_ctx, "untappedLands"):
            untapped_lands = decision_support_get(pilot_ctx, "untappedLands")
            assert isinstance(untapped_lands, int), f"untappedLands must be an int when present, got {untapped_lands!r}"
            ctx_parts.append(f"Untapped lands: {untapped_lands}")
        if decision_support_has(pilot_ctx, "landDropsUsed"):
            land_drops_used = decision_support_get(pilot_ctx, "landDropsUsed")
            assert isinstance(land_drops_used, int), (
                f"landDropsUsed must be an int when present, got {land_drops_used!r}"
            )
            remaining = 1 - land_drops_used
            ctx_parts.append(f"Land drops remaining: {remaining}")
        lines.append(f"  {', '.join(ctx_parts)}")

    # Message and choices/items
    raw_choices = _decision_value(decision, "choices")
    assert isinstance(raw_choices, list), f"decision choices must be a list, got {raw_choices!r}"
    choices = raw_choices
    items = _decision_optional(decision, "items")
    lines.append(f"  Message: {message if message else ''}")
    if items:
        assert isinstance(items, list), f"decision items must be a list when present, got {items!r}"
        total_min = _decision_optional(decision, "totalMin")
        total_max = _decision_optional(decision, "totalMax")
        header = f"  Items ({len(items)})"
        if total_min is not None and total_max is not None and total_min == total_max:
            header += f": total={total_min}"
        else:
            total_parts: list[str] = []
            if total_min is not None:
                total_parts.append(f"total_min={total_min}")
            if total_max is not None:
                total_parts.append(f"total_max={total_max}")
            if total_parts:
                header += f": {', '.join(total_parts)}"
        lines.append(header)
        for i, item in enumerate(items):
            assert isinstance(item, (dict, MultiAmountItem)), f"multi-amount item {i} must be an object, got {item!r}"
            assert decision_support_has(item, "description"), f"multi-amount item {i} missing 'description': {item}"
            desc = decision_support_get(item, "description")
            assert isinstance(desc, str), f"multi-amount item {i} description must be a string, got {desc!r}"
            constraints: list[str] = []
            if decision_support_has(item, "min"):
                constraints.append(f"min={decision_support_get(item, 'min')}")
            if decision_support_has(item, "max"):
                constraints.append(f"max={decision_support_get(item, 'max')}")
            suffix = f" [{', '.join(constraints)}]" if constraints else ""
            lines.append(f"    {i}: {desc}{suffix}")
    else:
        choice_descs = [_format_choice(c) for c in choices]
        lines.append(f"  Choices ({len(choices)}): {', '.join(choice_descs)}")

    # Triggered ability note
    if message and "Pick triggered ability" in message:
        lines.append(
            "  NOTE: This decision only determines the order triggered abilities"
            " are placed on the stack. Targets are chosen in separate decisions."
        )

    return "\n".join(lines)


def _render_board(snapshot: dict, deciding_player: str | None) -> str:
    """Render board state from snapshot players."""
    players_parts: list[str] = []
    for p in snapshot["players"]:
        name = p["name"]
        life = p["life"]
        bf = p.get("battlefield")
        gy = p.get("graveyard")
        exile = p.get("exile")
        hand = p.get("hand")

        # Hand: show full for deciding player, count only for opponents
        if deciding_player and name != deciding_player:
            hand_count = p["hand_count"] if "hand_count" in p else len(hand) if hand is not None else 0
            s = f"{name}: {life}hp"
            if hand_count:
                s += f" hand={hand_count}"
        else:
            hand_strs = [card_display(c) for c in hand] if hand else []
            s = f"{name}: {life}hp hand=[{', '.join(hand_strs)}]" if hand_strs else f"{name}: {life}hp hand=0"

        s += f" lib={p['library_size']}"

        # Player counters
        counters = p.get("counters")
        if counters:
            s += _format_counters(counters)

        if bf:
            bf_strs = [permanent_display(c) for c in bf]
            s += f" bf=[{', '.join(bf_strs)}]"
        if gy:
            gy_strs = [card_display(c) for c in gy]
            s += f" gy=[{', '.join(gy_strs)}]"
        if exile:
            exile_strs = [card_display(c) for c in exile]
            s += f" exile=[{', '.join(exile_strs)}]"

        players_parts.append(s)

    return " | ".join(players_parts)


def card_display(c: object) -> str:
    """Display a card (hand, graveyard, exile) as a string."""
    if not isinstance(c, str):
        return _record_name(c, source="card")
    return str(c)


def permanent_display(c: object) -> str:
    """Display a battlefield permanent with status annotations."""
    if isinstance(c, str):
        return str(c)
    name = _record_name(c, source="permanent")
    extras: list[str] = []
    if _record_field(c, "tapped"):
        extras.append("tapped")
    if _record_field(c, "summoning_sick"):
        extras.append("sick")
    if _record_field(c, "face_down"):
        extras.append("face_down")
    loyalty = _record_field(c, "loyalty")
    if loyalty is not None:
        extras.append(f"loyalty={loyalty}")
    counters = _record_field(c, "counters")
    if counters:
        if isinstance(counters, list):
            extras.extend(
                f"{ctr.get('name', '?')}={ctr.get('count', '?')}" for ctr in counters if isinstance(ctr, dict)
            )
        elif isinstance(counters, dict):
            for k, v in counters.items():
                extras.append(f"{k}={v}")
    original_card = _record_field(c, "original_card")
    if isinstance(original_card, str) and original_card:
        extras.append(f"copy of {original_card}")
    elif _record_field(c, "copy"):
        extras.append("copy")
    if _record_field(c, "token"):
        extras.append("token")
    # Power/toughness for creatures
    pt = _record_field(c, "power_toughness") or _record_field(c, "pt")
    power = _record_field(c, "power")
    toughness = _record_field(c, "toughness")
    if not pt and power is not None:
        pt = f"{power}/{toughness}"
    if pt:
        name += f" {pt}"
    if extras:
        return f"{name} ({', '.join(extras)})"
    return name


def _format_counters(counters: object) -> str:
    """Format player-level counters."""
    parts: list[str] = []
    if isinstance(counters, list):
        parts.extend(
            f" {ctr['name']}={ctr.get('count', '?')}" for ctr in counters if isinstance(ctr, dict) and ctr.get("name")
        )
    elif isinstance(counters, dict):
        for name, val in counters.items():
            parts.append(f" {name}={val}")
    return "".join(parts)


def _render_stack(stack: list) -> list[str]:
    """Render stack items."""
    parts: list[str] = []
    for item in stack:
        if isinstance(item, str):
            parts.append(item)
        else:
            desc = _render_stack_item(item)
            targets = _record_field(item, "targets")
            if targets:
                assert isinstance(targets, list), f"stack item targets must be a list, got {targets!r}"
                desc += " -> " + ", ".join(_render_stack_target(t) for t in targets)
            parts.append(desc)
    return parts


def _render_stack_item(item: object) -> str:
    """Render a stack item name with triggered-ability context when available."""
    source_card = _record_field(item, "source_card")
    ability_text = _record_field(item, "ability_text")
    if isinstance(source_card, str) and source_card and isinstance(ability_text, str) and ability_text:
        return f"{source_card} - {ability_text}"
    return _record_name(item, source="stack item")


def _render_stack_target(target: object) -> str:
    """Render a stack target as a readable name instead of a raw dict repr."""
    name = _record_field(target, "name")
    if isinstance(name, str) and name:
        return name
    return str(target)


def _render_combat(combat_groups: list) -> str:
    """Render combat groups."""
    parts: list[str] = []
    for group in combat_groups:
        attackers = group.get("attackers")
        atk_names: list[str] = []
        if attackers is not None:
            atk_names = [_record_name(a, source="combat attacker") for a in attackers if _record_field(a, "name")]
        blockers = group.get("blockers")
        blk_names: list[str] = []
        if blockers is not None:
            blk_names = [_record_name(b, source="combat blocker") for b in blockers if _record_field(b, "name")]
        part = ", ".join(atk_names)
        if blk_names:
            part += f" blocked by {', '.join(blk_names)}"
        elif group.get("blocked"):
            part += " (blocked)"
        if group.get("defending"):
            part += f" -> {group['defending']}"
        parts.append(part)
    return " | ".join(parts)


def _is_declare_blockers_phase(combat_phase: object) -> bool:
    """Return whether the pilot context is describing declare blockers."""
    return isinstance(combat_phase, str) and combat_phase.lower() in {"blockers", "declare_blockers"}


def _render_incoming_attackers(incoming_attackers: list) -> str:
    """Render incoming attackers with IDs for declare-blockers prompts."""
    parts: list[str] = []
    for attacker in incoming_attackers:
        if isinstance(attacker, str):
            parts.append(str(attacker))
            continue

        name = _record_name(attacker, source="incoming attacker")
        extras: list[str] = []
        attacker_id = _record_field(attacker, "id")
        if attacker_id:
            extras.append(f"id={attacker_id}")
        pt = _record_field(attacker, "power_toughness") or _record_field(attacker, "pt")
        power = _record_field(attacker, "power")
        toughness = _record_field(attacker, "toughness")
        if not pt and power is not None and toughness is not None:
            pt = f"{power}/{toughness}"
        if pt:
            extras.append(str(pt))

        if extras:
            parts.append(f"{name} [{', '.join(extras)}]")
        else:
            parts.append(name)

    return ", ".join(parts)


def _format_choice(c: object) -> str:
    """Format a single choice for display."""
    if isinstance(c, str):
        return c
    if not isinstance(c, (dict, Choice)):
        return str(c)
    raw_name = decision_support_get(c, "name")
    if isinstance(raw_name, str) and raw_name:
        name = raw_name
    else:
        raw_description = decision_support_get(c, "description")
        name = raw_description if isinstance(raw_description, str) and raw_description else "?"
    parts: list[str] = [name]
    choice_id = decision_support_get(c, "id")
    if isinstance(choice_id, str) and choice_id:
        parts.append(f"id={choice_id}")
    action = decision_support_get(c, "action")
    if isinstance(action, str) and action:
        parts.append(action)
    mana_cost = decision_support_get(c, "mana_cost")
    if isinstance(mana_cost, str) and mana_cost:
        parts.append(mana_cost)
    if len(parts) > 1:
        return f"{parts[0]} [{', '.join(parts[1:])}]"
    return parts[0]


def _render_chosen_block(decision: DecisionLike, snapshot: dict | None = None) -> str:
    """Render what was chosen in a decision."""
    lines: list[str] = []
    chosen = _decision_optional(decision, "chosen")
    raw_chosen_args = _decision_optional(decision, "chosenArgs")
    if raw_chosen_args is not None:
        assert isinstance(raw_chosen_args, dict), f"chosenArgs must be an object when present, got {raw_chosen_args!r}"
        chosen_args = raw_chosen_args
    else:
        chosen_args = {}
    raw_choices = _decision_value(decision, "choices")
    assert isinstance(raw_choices, list), f"decision choices must be a list, got {raw_choices!r}"
    choices = raw_choices

    # Display chosen
    chosen_name = _chosen_display(chosen, chosen_args, choices)
    lines.append(f"  Chosen: {chosen_name}")

    # Show mana plan if the player specified one
    mana_plan = chosen_args.get("mana_plan")
    if mana_plan:
        resolved = _resolve_mana_plan(mana_plan, snapshot)
        plan_str = f"  Mana plan: {resolved}"
        if chosen_args.get("auto_tap") is False:
            plan_str += " (auto_tap=false)"
        lines.append(plan_str)

    # Show targeting / activation details from subsequent actions.
    # These are part of the decision itself (what the player targeted), not
    # outcome information, so they're safe to include without biasing the annotator.
    player = _decision_value(decision, "player")
    assert isinstance(player, str), f"decision player must be a string, got {player!r}"
    subsequent_actions = _decision_value(decision, "subsequentActions")
    assert isinstance(subsequent_actions, list), (
        f"decision subsequentActions must be a list, got {subsequent_actions!r}"
    )
    for action in subsequent_actions:
        if not action.startswith(player):
            continue
        if " targeting " in action or "activates:" in action:
            lines.append(f"  Result: {action}")
            break

    if _decision_optional(decision, "castRolledBack"):
        lines.append(
            "  **NOTE:** This cast was attempted but the game engine rolled it "
            "back because the player could not complete the mana payment."
        )

    return "\n".join(lines)


def _resolve_mana_plan(mana_plan: object, snapshot: dict | None) -> str:
    """Resolve mana_plan entries to readable names using the snapshot."""
    # Build ID -> name map from battlefield permanents
    id_to_name: dict[str, str] = {}
    if snapshot:
        for p in snapshot["players"]:
            battlefield = p.get("battlefield")
            if battlefield is not None:
                for perm in battlefield:
                    perm_id = _record_field(perm, "id")
                    if isinstance(perm_id, str) and perm_id:
                        perm_name = _record_field(perm, "name")
                        id_to_name[perm_id] = perm_name if isinstance(perm_name, str) and perm_name else perm_id

    parts: list[str] = []
    if isinstance(mana_plan, str):
        raw_entries: Sequence[object] = mana_plan.split(",")
    else:
        assert isinstance(mana_plan, list), f"mana_plan must be a CSV string or list, got {mana_plan!r}"
        raw_entries = mana_plan

    for raw_entry in raw_entries:
        entry = _render_mana_plan_entry(raw_entry, id_to_name)
        if entry:
            parts.append(entry)

    return ", ".join(parts)


def _render_mana_plan_entry(entry: object, id_to_name: dict[str, str]) -> str:
    """Render one mana_plan entry from either flat or structured form."""
    if isinstance(entry, str):
        token = entry.strip()
        if not token:
            return ""
        return _render_mana_plan_token(token, id_to_name)

    assert isinstance(entry, dict), f"mana_plan entry must be str or dict, got {entry!r}"
    keys = set(entry)
    assert keys in ({"tap"}, {"pool"}), (
        f"mana_plan dict entry must contain exactly one of 'tap' or 'pool', got {entry!r}"
    )

    if "tap" in entry:
        tap = entry["tap"]
        assert isinstance(tap, str), f"mana_plan tap target must be a string, got {tap!r}"
        token = tap.strip()
        assert token, f"mana_plan tap target must be non-empty, got {entry!r}"
        return _render_mana_plan_token(token, id_to_name)

    pool = entry["pool"]
    assert isinstance(pool, str), f"mana_plan pool color must be a string, got {pool!r}"
    token = pool.strip()
    assert token, f"mana_plan pool color must be non-empty, got {entry!r}"
    return token


def _render_mana_plan_token(token: str, id_to_name: dict[str, str]) -> str:
    """Resolve a flat mana_plan token like 'p1', 'p5:1', or 'RED'."""
    base_id = token.split(":")[0]
    name = id_to_name.get(base_id)
    if name:
        return f"{name} ({token})"
    return token


def _chosen_display(
    chosen: object,
    chosen_args: dict | None,
    choices: list,
) -> str:
    """Format what was chosen for display."""
    if chosen is None:
        if not chosen_args:
            return "(no response)"
        # Batch attack/block declarations and text choices store the response
        # in chosen_args, not in chosen.  Render them instead of "(no response)".
        attackers = chosen_args.get("attackers")
        if attackers:
            return _batch_attack_display(attackers, choices)
        blockers = chosen_args.get("blockers")
        if blockers:
            return _batch_block_display(blockers, choices)
        text = chosen_args.get("text")
        if text:
            return f"Text: {text}"
        return "(no response)"
    if isinstance(chosen, bool):
        return str(chosen)
    if isinstance(chosen, int) and 0 <= chosen < len(choices):
        c = choices[chosen]
        if isinstance(c, (dict, Choice)):
            name = decision_support_get(c, "name")
            if isinstance(name, str) and name:
                return name
            description = decision_support_get(c, "description")
            if isinstance(description, str) and description:
                return description
            return str(chosen)
        return str(c)
    return str(chosen)


def _attacker_id(entry: object) -> str:
    """Extract an attacker ID from either string or dict form."""
    if isinstance(entry, dict):
        return str(entry.get("id", entry))
    return str(entry)


def _batch_attack_display(attackers: list | str, choices: list) -> str:
    """Render a batch attack declaration for display."""
    # Handle comma-separated string format (epoch 36+)
    if isinstance(attackers, str):
        attackers = [a.strip() for a in attackers.split(",")]
    if attackers == ["all"]:
        # Resolve names from choices (exclude the "All attack" special entry)
        names = []
        for c in choices:
            if not isinstance(c, (dict, Choice)):
                continue
            if decision_support_get(c, "id") == "all":
                continue
            name = decision_support_get(c, "name")
            names.append(name if isinstance(name, str) and name else str(c))
        if names:
            return f"Attack with all ({', '.join(names)})"
        return "Attack with all creatures"
    # Resolve individual attacker IDs to names (entries may be strings or dicts)
    choice_by_id: dict[str, str] = {}
    for c in choices:
        if not isinstance(c, (dict, Choice)):
            continue
        choice_id = decision_support_get(c, "id")
        if not isinstance(choice_id, str) or not choice_id:
            continue
        name = decision_support_get(c, "name")
        choice_by_id[choice_id] = name if isinstance(name, str) and name else choice_id
    names = [choice_by_id.get(_attacker_id(a), _attacker_id(a)) for a in attackers]
    return f"Attack with {', '.join(names)}"


def _batch_block_display(blockers: list | str, choices: list) -> str:
    """Render a batch block declaration for display.

    Handles four persisted formats:
    - Comma-separated "blocker:attacker" string (epoch 36+)
    - List of "blocker_id:attacker_id" strings
    - JSON-encoded string of the above list (legacy)
    - List of {"id": blocker_id, "blocks": attacker_id} dicts (legacy)
    """
    if not blockers:
        return "No blocks"
    if isinstance(blockers, str):
        # Try JSON first (legacy format), then comma-separated (epoch 36+)
        try:
            parsed = json.loads(blockers)
        except (json.JSONDecodeError, TypeError):
            parsed = [b.strip() for b in blockers.split(",")]
        blockers = parsed
    choice_by_id: dict[str, str] = {}
    for c in choices:
        if not isinstance(c, (dict, Choice)):
            continue
        choice_id = decision_support_get(c, "id")
        if not isinstance(choice_id, str) or not choice_id:
            continue
        name = decision_support_get(c, "name")
        choice_by_id[choice_id] = name if isinstance(name, str) and name else choice_id
    parts = []
    for entry in blockers:
        if isinstance(entry, dict):
            # Legacy dict form: {"id": "p5", "blocks": "p17"}
            blocker_id = str(entry.get("id", "?"))
            attacker_id = str(entry.get("blocks", "?"))
            blocker_name = choice_by_id.get(blocker_id, blocker_id)
            attacker_name = choice_by_id.get(attacker_id, attacker_id)
            parts.append(f"{blocker_name} blocks {attacker_name}")
        elif isinstance(entry, str) and ":" in entry:
            blocker_id, attacker_id = entry.split(":", 1)
            blocker_name = choice_by_id.get(blocker_id, blocker_id)
            attacker_name = choice_by_id.get(attacker_id, attacker_id)
            parts.append(f"{blocker_name} blocks {attacker_name}")
        else:
            parts.append(str(choice_by_id.get(str(entry), str(entry))))
    return ", ".join(parts)


def _render_card_reference(
    decision: DecisionLike,
    snapshot: dict,
    oracle_texts: dict[str, dict],
) -> str:
    """Build a Card Reference section for non-basic cards in the decision."""
    # Collect all card names from snapshot and choices
    names: set[str] = set()
    for p in snapshot["players"]:
        for zone in ("hand", "battlefield", "graveyard", "exile", "commanders"):
            zone_cards = p.get(zone)
            if zone_cards is None:
                continue
            for c in zone_cards:
                if isinstance(c, str) and c:
                    names.add(c)
                else:
                    name = _record_field(c, "name")
                    if isinstance(name, str) and name:
                        names.add(name)
    stack = snapshot.get("stack")
    if stack is not None:
        for item in stack:
            if isinstance(item, str) and item:
                names.add(item)
            else:
                name = _record_field(item, "name")
                if isinstance(name, str) and name:
                    names.add(name)
    raw_choices = _decision_value(decision, "choices")
    assert isinstance(raw_choices, list), f"decision choices must be a list, got {raw_choices!r}"
    for c in raw_choices:
        if isinstance(c, (dict, Choice)):
            name = decision_support_get(c, "name")
            if isinstance(name, str) and name:
                names.add(name)

    # Filter to non-basic cards with oracle text
    lines: list[str] = []
    for name in sorted(names):
        if name in BASIC_LAND_NAMES:
            continue
        oracle = oracle_texts.get(name)
        if not oracle:
            continue
        mana_cost = oracle.get("mana_cost")
        type_line = oracle.get("type_line")
        oracle_text = oracle.get("oracle_text")
        pt = oracle.get("power_toughness")
        if not pt and oracle.get("power") is not None:
            pt = f"{oracle['power']}/{oracle['toughness']}"

        entry = f"- {name}"
        if mana_cost:
            entry += f" {mana_cost}"
        if type_line:
            entry += f" -- {type_line}"
        if pt:
            entry += f" {pt}"
        if oracle_text:
            # Condense multi-line oracle text
            text = oracle_text.replace("\n", " / ")
            entry += f": {text}"
        lines.append(entry)

    if not lines:
        return ""
    return "## Card Reference\n" + "\n".join(lines)
