"""Game-context helpers for blunder analysis prompt construction."""

import html
import json
import re
from collections.abc import Sequence

from magebench.game import scryfall
from magebench.game.decision_renderer import card_display, permanent_display
from magebench.game.game_export_types import (
    Action,
    BuiltGameExport,
    Decision,
    GameExport,
    Permanent,
    Snapshot,
    SnapshotPlayer,
    export_record_field,
)

get_oracle_texts = scryfall.get_oracle_texts


def _record_field(record: object, field: str) -> object | None:
    return export_record_field(record, field)


_SNAPSHOT_ZONES = frozenset({"hand", "battlefield", "graveyard", "exile", "commanders"})


def _snapshot_zone_cards(
    player: SnapshotPlayer, zone: str
) -> list[str | Permanent] | None:
    """Return a snapshot player's cards for a supported public/private zone."""
    assert zone in _SNAPSHOT_ZONES, f"unexpected zone {zone!r}"
    cards: list[str | Permanent] | None = getattr(player, zone)
    return cards


def collect_card_names(data: BuiltGameExport | GameExport) -> set[str]:
    """Collect all unique card names from game snapshots and choices."""
    names: set[str] = set()
    for snap in data.snapshots:
        for p in snap.players:
            for zone in ("hand", "battlefield", "graveyard", "exile", "commanders"):
                zone_cards = _snapshot_zone_cards(p, zone)
                if zone_cards is not None:
                    for c in zone_cards:
                        if isinstance(c, str) and c:
                            names.add(c)
                        else:
                            name = _record_field(c, "name")
                            if isinstance(name, str) and name:
                                names.add(name)
        for item in snap.stack:
            if isinstance(item, str) and item:
                names.add(item)
            else:
                name = _record_field(item, "name")
                if isinstance(name, str) and name:
                    names.add(name)
        if snap.combat is not None:
            for group in snap.combat:
                if group.attackers is not None:
                    for a in group.attackers:
                        name = _record_field(a, "name")
                        if isinstance(name, str) and name:
                            names.add(name)
                if group.blockers is not None:
                    for b in group.blockers:
                        name = _record_field(b, "name")
                        if isinstance(name, str) and name:
                            names.add(name)
    # Also from choice names and combat fields in llm events
    for ev in data.llm_events:
        if ev.type == "tool_call" and ev.tool == "get_action_choices":
            try:
                result = json.loads(ev.result)
                if not isinstance(result, dict):
                    continue
                result_choices = result.get("choices")
                if result_choices is not None:
                    for c in result_choices:
                        if not isinstance(c, dict):
                            continue
                        name = c.get("name")
                        # Skip non-card choices: player targets, special actions,
                        # and entries without an id (e.g. mana ability descriptions)
                        if (
                            not isinstance(name, str)
                            or not name
                            or "target_type" in c
                            or c.get("choice_type") == "special"
                        ):
                            continue
                        if "id" in c:
                            names.add(name)
                result_already_attacking = result.get("already_attacking")
                if result_already_attacking is not None:
                    for a in result_already_attacking:
                        if (
                            isinstance(a, dict)
                            and isinstance(a.get("name"), str)
                            and a["name"]
                        ):
                            names.add(a["name"])
                result_incoming = result.get("incoming_attackers")
                if result_incoming is not None:
                    for a in result_incoming:
                        if (
                            isinstance(a, dict)
                            and isinstance(a.get("name"), str)
                            and a["name"]
                        ):
                            names.add(a["name"])
                result_combat = result.get("combat")
                if result_combat is not None:
                    for group in result_combat:
                        if not isinstance(group, dict):
                            continue
                        group_attackers = group.get("attackers")
                        if group_attackers is not None:
                            for a in group_attackers:
                                if (
                                    isinstance(a, dict)
                                    and isinstance(a.get("name"), str)
                                    and a["name"]
                                ):
                                    names.add(a["name"])
                        group_blockers = group.get("blockers")
                        if group_blockers is not None:
                            for b in group_blockers:
                                if (
                                    isinstance(b, dict)
                                    and isinstance(b.get("name"), str)
                                    and b["name"]
                                ):
                                    names.add(b["name"])
            except (json.JSONDecodeError, TypeError):
                pass
    # Filter out tokens (not in Scryfall)
    return {n for n in names if "Token" not in n}


_ACTION_NOISE = re.compile(
    r" draws a card$"
    r"|^spectator\d+ has started watching$"
    r"| skip attack$"
    r"| keeps hand$"
    r"| skips Draw step$"
    r"| puts .+ from stack (onto the Battlefield|into their graveyard)$"
    r"| puts .+ from hand onto the Battlefield$"
)


def actions_by_turn(actions: Sequence[Action]) -> dict[int, list[str]]:
    """Split action log messages into per-turn buckets using TURN markers.

    Rewrites TURN headers from XMage's sequential numbering to per-player
    turn numbers: "TURN 5 for Alice (20 - 18)" → "Alice turn 3 (20 - 18)".
    Filters out noisy/redundant messages (draw step, skip attack, zone moves).
    """
    by_turn: dict[int, list[str]] = {}
    current_turn = 0
    player_turn_counts: dict[str, int] = {}
    for a in actions:
        msg = a.message
        if msg is None:
            continue
        assert isinstance(msg, str), f"action message must be a string, got {msg!r}"
        # Skip chat messages — LLM personality flavor adds noise and can bias
        # the blunder annotator
        if a.type == "chat":
            continue
        msg = html.unescape(msg)
        m = re.match(r"^TURN (\d+) for (.+?)( \(.+\))$", msg)
        if m:
            current_turn = int(m.group(1))
            player_name = m.group(2)
            life_info = m.group(3)
            player_turn_counts[player_name] = player_turn_counts.get(player_name, 0) + 1
            pt = player_turn_counts[player_name]
            msg = f"{player_name} turn {pt}{life_info}"
        elif _ACTION_NOISE.search(msg):
            continue
        if current_turn > 0 and msg:
            by_turn.setdefault(current_turn, []).append(msg)
    return by_turn


def _snapshot_for_turn(snapshots: Sequence[Snapshot], turn: int) -> Snapshot | None:
    """Find the first snapshot for a given turn number."""
    for snap in snapshots:
        if snap.turn == turn:
            return snap
    return None


def format_prior_context(
    decision: Decision,
    snapshots: Sequence[Snapshot],
    actions_by_turn: dict[int, list[str]],
    num_players: int,
) -> str:
    """Build prior context: snapshot from 2 turn cycles ago + action deltas.

    A turn cycle = one turn per player. So 2 cycles back = 2 * num_players
    turn numbers back from the current turn.
    """
    current_turn = decision.turn
    lookback = 2 * num_players
    if not current_turn or current_turn <= lookback:
        return ""

    ref_turn = current_turn - lookback
    ref_snap = _snapshot_for_turn(snapshots, ref_turn)
    if ref_snap is None:
        return ""

    # Format the reference snapshot using shared renderer display functions
    players_parts: list[str] = []
    for p in ref_snap.players:
        bf = p.battlefield
        s = f"{p.name}: {p.life}hp"
        if bf:
            s += f" bf=[{', '.join(permanent_display(x) for x in bf)}]"
        gy = p.graveyard
        if gy:
            s += f" gy=[{', '.join(card_display(x) for x in gy)}]"
        players_parts.append(s)

    lines = ["## Prior Context (2 turn cycles ago)\n"]
    lines.append(f"Board: {' | '.join(players_parts)}")

    # Add action deltas for turns ref_turn through current_turn - 1
    lines.append("")
    for t in range(ref_turn, current_turn):
        turn_actions = actions_by_turn.get(t)
        if turn_actions is not None:
            lines.extend(turn_actions)

    return "\n".join(lines)


def format_current_turn_actions(
    decision: Decision,
    all_actions: Sequence[Action],
    cutoff_ts: str | None,
) -> str:
    """Format actions from the current turn before this decision.

    Helps the LLM see what happened (or didn't happen) this turn,
    e.g. whether a land was already played or spells were cast.
    """
    current_turn = decision.turn
    if not current_turn or not cutoff_ts:
        return ""

    in_current_turn = False
    lines: list[str] = []
    for a in all_actions:
        msg = a.message
        ts = a.ts
        if msg is None:
            continue
        assert isinstance(msg, str), f"action message must be a string, got {msg!r}"
        assert isinstance(ts, str) or ts is None, (
            f"action ts must be a string when present, got {ts!r}"
        )

        # Track TURN markers to find current turn boundaries
        m = re.match(r"^TURN (\d+) for", msg)
        if m:
            turn_num = int(m.group(1))
            if turn_num == current_turn:
                in_current_turn = True
                continue
            if turn_num > current_turn:
                break
            in_current_turn = False
            continue

        if not in_current_turn:
            continue

        # Only actions before the decision was presented
        if ts and ts >= cutoff_ts:
            break

        # Skip chat messages — LLM personality flavor adds noise
        if a.type == "chat":
            continue
        msg = html.unescape(msg)

        # Filter noise (same as prior context)
        if _ACTION_NOISE.search(msg):
            continue

        if msg:
            lines.append(msg)

    if not lines:
        return "## This Turn\n(no actions yet)"

    return "## This Turn\n" + "\n".join(lines)


def game_overview(data: BuiltGameExport | GameExport) -> str:
    lines = [
        f"Game: {data.id}",
        f"Format: {data.deck_type} ({data.game_type})",
    ]
    for p in data.players:
        lines.append(f"  {p.name} ({p.model or '?'})")
        if p.deck_strategy:
            lines.append(f"    Deck: {p.deck_strategy}")
    return "\n".join(lines)
