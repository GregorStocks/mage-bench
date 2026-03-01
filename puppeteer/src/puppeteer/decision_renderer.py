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


def render_decision(
    decision: dict,
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
        chosen_block = _render_chosen_block(decision)
        if chosen_block:
            decision_parts.append(chosen_block)

    parts.append("\n\n".join(decision_parts))

    return "\n\n".join(parts)


def _render_decision_block(
    decision: dict,
    snapshot: dict,
    deciding_player: str | None,
) -> str:
    """Render the core decision: board state, stack, choices."""
    turn = decision.get("turn", "?")
    phase = decision.get("phase") or ("PREGAME" if turn == 0 else "?")
    player = decision.get("player", "?")
    message = decision.get("message", "")

    # Header
    lines: list[str] = [
        f"[Decision {decision.get('index', '?')}, snapshot={decision.get('snapshotIndex', '?')}] "
        f"Turn {turn} {phase} - {player}"
    ]

    # Board state from snapshot
    board_line = _render_board(snapshot, deciding_player)
    lines.append(f"  Board: {board_line}")

    # Stack
    stack = snapshot.get("stack", [])
    if stack:
        stack_parts = _render_stack(stack)
        lines.append(f"  Stack: [{', '.join(stack_parts)}]")

    # Combat
    combat_groups = snapshot.get("combat", []) or decision.get("pilotContext", {}).get("combat", [])
    if combat_groups:
        combat_line = _render_combat(combat_groups)
        lines.append(f"  Combat: {combat_line}")

    combat_phase = decision.get("pilotContext", {}).get("combatPhase")
    if combat_phase:
        lines.append(f"  Combat Phase: {combat_phase}")

    # Pilot context overlay
    pilot_ctx = decision.get("pilotContext", {})
    if "untappedLands" in pilot_ctx or "landDropsUsed" in pilot_ctx:
        ctx_parts: list[str] = []
        if "untappedLands" in pilot_ctx:
            ctx_parts.append(f"Untapped lands: {pilot_ctx['untappedLands']}")
        if "landDropsUsed" in pilot_ctx:
            remaining = 1 - pilot_ctx["landDropsUsed"]
            ctx_parts.append(f"Land drops remaining: {remaining}")
        lines.append(f"  {', '.join(ctx_parts)}")

    # Message and choices
    choices = decision.get("choices", [])
    lines.append(f"  Message: {message}")
    choice_descs = [_format_choice(c) for c in choices]
    lines.append(f"  Choices ({len(choices)}): {', '.join(choice_descs)}")

    # Triggered ability note
    if "Pick triggered ability" in message:
        lines.append(
            "  NOTE: This decision only determines the order triggered abilities"
            " are placed on the stack. Targets are chosen in separate decisions."
        )

    return "\n".join(lines)


def _render_board(snapshot: dict, deciding_player: str | None) -> str:
    """Render board state from snapshot players."""
    players_parts: list[str] = []
    for p in snapshot.get("players", []):
        name = p.get("name", "?")
        life = p.get("life", "?")
        bf = p.get("battlefield", [])
        gy = p.get("graveyard", [])
        exile = p.get("exile", [])

        # Hand: show full for deciding player, count only for opponents
        if deciding_player and name != deciding_player:
            hand_count = p.get("hand_count", p.get("hand_size", len(p.get("hand", []))))
            s = f"{name}: {life}hp"
            if hand_count:
                s += f" hand={hand_count}"
        else:
            hand = p.get("hand", [])
            hand_strs = [card_display(c) for c in hand]
            s = f"{name}: {life}hp hand=[{', '.join(hand_strs)}]" if hand_strs else f"{name}: {life}hp hand=0"

        lib = p.get("library_size")
        if lib is not None:
            s += f" lib={lib}"

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
    if isinstance(c, dict):
        return c.get("name", "?")
    return str(c)


def permanent_display(c: object) -> str:
    """Display a battlefield permanent with status annotations."""
    if not isinstance(c, dict):
        return str(c)
    name = c.get("name", "?")
    extras: list[str] = []
    if c.get("tapped"):
        extras.append("tapped")
    if c.get("summoning_sick"):
        extras.append("sick")
    if c.get("face_down"):
        extras.append("face_down")
    if c.get("loyalty") is not None:
        extras.append(f"loyalty={c['loyalty']}")
    if c.get("counters"):
        counters = c["counters"]
        if isinstance(counters, list):
            for ctr in counters:
                if isinstance(ctr, dict):
                    extras.append(f"{ctr.get('name', '?')}={ctr.get('count', '?')}")
        elif isinstance(counters, dict):
            for k, v in counters.items():
                extras.append(f"{k}={v}")
    if c.get("original_card"):
        extras.append(f"copy of {c['original_card']}")
    elif c.get("copy"):
        extras.append("copy")
    if c.get("token"):
        extras.append("token")
    # Power/toughness for creatures
    pt = c.get("power_toughness") or c.get("pt")
    if not pt and c.get("power") is not None:
        pt = f"{c['power']}/{c['toughness']}"
    if pt:
        name += f" {pt}"
    if extras:
        return f"{name} ({', '.join(extras)})"
    return name


def _format_counters(counters: object) -> str:
    """Format player-level counters."""
    parts: list[str] = []
    if isinstance(counters, list):
        for ctr in counters:
            if isinstance(ctr, dict) and ctr.get("name"):
                parts.append(f" {ctr['name']}={ctr.get('count', '?')}")
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
        elif isinstance(item, dict):
            desc = item.get("name", "?")
            targets = item.get("targets", [])
            if targets:
                desc += " -> " + ", ".join(str(t) for t in targets)
            parts.append(desc)
        else:
            parts.append(str(item))
    return parts


def _render_combat(combat_groups: list) -> str:
    """Render combat groups."""
    parts: list[str] = []
    for group in combat_groups:
        atk_names = [a["name"] for a in group.get("attackers", []) if isinstance(a, dict) and a.get("name")]
        blk_names = [b["name"] for b in group.get("blockers", []) if isinstance(b, dict) and b.get("name")]
        part = ", ".join(atk_names)
        if blk_names:
            part += f" blocked by {', '.join(blk_names)}"
        elif group.get("blocked"):
            part += " (blocked)"
        if group.get("defending"):
            part += f" -> {group['defending']}"
        parts.append(part)
    return " | ".join(parts)


def _format_choice(c: object) -> str:
    """Format a single choice for display."""
    if isinstance(c, str):
        return c
    if not isinstance(c, dict):
        return str(c)
    name: str = c.get("name") or c.get("description") or "?"
    parts: list[str] = [name]
    if c.get("id"):
        parts.append(f"id={c['id']}")
    if c.get("action"):
        parts.append(c["action"])
    if c.get("mana_cost"):
        parts.append(c["mana_cost"])
    if len(parts) > 1:
        return f"{parts[0]} [{', '.join(parts[1:])}]"
    return parts[0]


def _render_chosen_block(decision: dict) -> str:
    """Render what was chosen in a decision."""
    lines: list[str] = []
    chosen = decision.get("chosen")
    chosen_args = decision.get("chosenArgs", {})
    choices = decision.get("choices", [])

    # Display chosen
    chosen_name = _chosen_display(chosen, chosen_args, choices)
    lines.append(f"  Chosen: {chosen_name}")

    # Show targeting / activation details from subsequent actions.
    # These are part of the decision itself (what the player targeted), not
    # outcome information, so they're safe to include without biasing the annotator.
    player = decision.get("player", "")
    for action in decision.get("subsequentActions", []):
        if not action.startswith(player):
            continue
        if " targeting " in action or "activates:" in action:
            lines.append(f"  Result: {action}")
            break

    if decision.get("castRolledBack"):
        lines.append(
            "  **NOTE:** This cast was attempted but the game engine rolled it "
            "back because the player could not complete the mana payment."
        )

    return "\n".join(lines)


def _chosen_display(
    chosen: object,
    chosen_args: dict,
    choices: list,
) -> str:
    """Format what was chosen for display."""
    if chosen is None:
        # Batch attack/block declarations store the response in chosen_args,
        # not in chosen.  Render them instead of "(no response)".
        attackers = chosen_args.get("attackers")
        if attackers:
            return _batch_attack_display(attackers, choices)
        blockers = chosen_args.get("blockers")
        if blockers:
            return _batch_block_display(blockers, choices)
        return "(no response)"
    if isinstance(chosen, bool):
        return str(chosen)
    if isinstance(chosen, int) and 0 <= chosen < len(choices):
        c = choices[chosen]
        if isinstance(c, dict):
            return str(c.get("name") or c.get("description") or chosen)
        return str(c)
    return str(chosen)


def _attacker_id(entry: object) -> str:
    """Extract an attacker ID from either string or dict form."""
    if isinstance(entry, dict):
        return str(entry.get("id", entry))
    return str(entry)


def _batch_attack_display(attackers: list, choices: list) -> str:
    """Render a batch attack declaration for display."""
    if attackers == ["all"]:
        # Resolve names from choices (exclude the "All attack" special entry)
        names = [c.get("name", str(c)) for c in choices if isinstance(c, dict) and c.get("id") != "all"]
        if names:
            return f"Attack with all ({', '.join(names)})"
        return "Attack with all creatures"
    # Resolve individual attacker IDs to names (entries may be strings or dicts)
    choice_by_id = {c["id"]: c.get("name", c["id"]) for c in choices if isinstance(c, dict) and "id" in c}
    names = [choice_by_id.get(_attacker_id(a), _attacker_id(a)) for a in attackers]
    return f"Attack with {', '.join(names)}"


def _batch_block_display(blockers: list | str, choices: list) -> str:
    """Render a batch block declaration for display.

    Handles three persisted formats:
    - List of "blocker_id:attacker_id" strings (current)
    - JSON-encoded string of the above list (legacy)
    - List of {"id": blocker_id, "blocks": attacker_id} dicts (legacy)
    """
    if not blockers:
        return "No blocks"
    # Legacy: JSON-encoded string
    if isinstance(blockers, str):
        try:
            blockers = json.loads(blockers)
        except (json.JSONDecodeError, TypeError):
            return f"Block ({blockers})"
    choice_by_id = {c["id"]: c.get("name", c["id"]) for c in choices if isinstance(c, dict) and "id" in c}
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
    decision: dict,
    snapshot: dict,
    oracle_texts: dict[str, dict],
) -> str:
    """Build a Card Reference section for non-basic cards in the decision."""
    # Collect all card names from snapshot and choices
    names: set[str] = set()
    for p in snapshot.get("players", []):
        for zone in ("hand", "battlefield", "graveyard", "exile", "commanders"):
            for c in p.get(zone, []):
                if isinstance(c, dict):
                    name = c.get("name", "")
                    if name:
                        names.add(name)
                elif isinstance(c, str) and c:
                    names.add(c)
    for item in snapshot.get("stack", []):
        if isinstance(item, dict) and item.get("name"):
            names.add(item["name"])
        elif isinstance(item, str) and item:
            names.add(item)
    for c in decision.get("choices", []):
        if isinstance(c, dict) and c.get("name"):
            names.add(c["name"])

    # Filter to non-basic cards with oracle text
    lines: list[str] = []
    for name in sorted(names):
        if name in BASIC_LAND_NAMES:
            continue
        oracle = oracle_texts.get(name)
        if not oracle:
            continue
        mana_cost = oracle.get("mana_cost", "")
        type_line = oracle.get("type_line", "")
        oracle_text = oracle.get("oracle_text", "")
        pt = oracle.get("power_toughness", "")
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
