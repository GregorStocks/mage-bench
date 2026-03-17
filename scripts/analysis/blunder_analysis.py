#!/usr/bin/env python3
"""Analyze a game for blunders using Opus 4.6 via OpenRouter.

Per-decision approach: sends each non-forced decision to Opus individually
for high-quality blunder detection.

Usage:
    uv run --project puppeteer python scripts/analysis/blunder_analysis.py <game.json.gz | game_id>

Accepts either a file path or a bare game ID (e.g. game_20260214_185313_g1).

Requires OPENROUTER_API_KEY environment variable.
"""

import html
import json
import logging
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from openai import OpenAI, OpenAIError

from puppeteer.decision_renderer import (
    _chosen_display as _renderer_chosen_display,
)
from puppeteer.decision_renderer import (
    card_display,
    permanent_display,
    render_decision,
)
from puppeteer.llm_cost import fetch_openrouter_prices, get_model_price
from schemas.game_export_types import Action, GameExport, Snapshot, SnapshotPlayer
from scripts import scryfall
from scripts.analysis.annotate_game import annotate_game
from scripts.analysis.blunder_eval_common import (
    action_result,
    decision_index,
    is_canonical_decision,
    is_cast_rolled_back,
    is_forced,
    is_mana_ability_subdecision,
    is_rolled_back,
    load_game,
    snapshot_index,
)
from scripts.analysis.extract_decisions import extract_decisions

# Suppress httpx's per-request INFO logging (e.g. "HTTP Request: POST ... 200 OK")
logging.getLogger("httpx").setLevel(logging.WARNING)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TMP_DIR = REPO_ROOT / "tmp"

# Model (OpenRouter ID)
OPUS_MODEL = "anthropic/claude-opus-4.6"
BASE_URL = "https://openrouter.ai/api/v1"

# Max parallel API calls for per-decision analysis.
# OpenRouter rate limits scale with account balance ($1 = 1 RPS, max 500 RPS),
# so 50 concurrent requests is well within limits. The openai SDK retries 429s
# automatically with exponential backoff.
MAX_WORKERS = 50
_LOG_TZ = ZoneInfo("America/Los_Angeles")

# Bump this when the analysis pipeline changes enough to warrant re-running.
# Games analyzed with an older version will be automatically re-analyzed.
# v1: initial two-phase pipeline (Haiku pre-filter + Opus analysis)
# v2: softened Haiku prompt + Opus calibration check for zero-flag games
# v3: add "questionable" severity, fix Opus dismissal bias, better category examples
# v4: switch pre-filter from Haiku to Sonnet (more mechanically specific flags)
# v5: single-phase Opus (no pre-filter, cheaper, better coverage, 1M context)
# v6: per-decision Sonnet 4.5 + low thinking (approach P from experiment)
# v7: include stack, graveyard contents, exile contents in decision context
# v8: include Scryfall oracle text in per-decision prompt
# v9: switch from Sonnet 4.5 (thinking=low) to Opus 4.6 (no extended thinking)
# v10: add prior context (snapshot from 2 turns ago + action deltas)
# v11: filter out failed (success=false), cancelled, and cast-before-cancel decisions
# v12: add current-turn action context (no prompt additions, just context)
# v13: fix card name extraction for dict-form permanents (tapped/counters)
# v14: fix play/draw decision seeing dealt hands (no snapshot before hands dealt)
# v15: include combat context (attackers/blockers) in per-decision prompt
# v16: detect rolled-back casts (mana payment failures) — skip intermediate
#      decisions, add context to the initiating cast decision
# v17: enrich decision context — remove battlefield/choice caps, add library
#      sizes, player counters, structured choice info (action, mana_cost, P/T, id)
# v18: include stack targets in decision context (e.g. "Lightning Bolt -> Goblin Guide")
# v19: clarify "Pick triggered ability" decisions are about ordering, not targeting
# v20: add explicit guidance about passing priority in postcombat main with
#      sorcery-speed actions remaining (land drops, sorceries, creatures)
# v21: fix snapshot lookup for events missing gameSeq (e.g. discard-to-hand-size),
#      which were falling back to snapshot 0 and showing turn=? phase=? to the LLM
# v22: filter subsequent_actions ("After:") to only show the deciding player's
#      own actions, not opponent actions — prevents leaking future information
#      about what the opponent did while still showing what the player followed
#      up with (e.g. played a land, cast a spell)
# v23: moved static instructions (examples, severity, output format) from
#      user message to system prompt
# v24: clarify that choices list = legal actions in pass-priority guidance
# v25: improve prompt structure — remove After/Reasoning from chosen block,
#      restructure sections (Card Reference / Prior Context / This Turn / Decision),
#      add "Chosen: False" guidance, fix PREGAME phase, prefix chat messages,
#      enrich permanent display (loyalty, token, copy), fix prior context board rendering
# v26: add "(no response)" guidance, fix land drops display ambiguity,
#      filter mana sub-decisions and chat messages from context,
#      show targeting/activation details in chosen block
# v27: fix is_forced false positives — boolean questions and single-choice
#      selects with pass option are no longer skipped
# v28: add deck archetype/strategy context to game overview when available
# v29: fix batch attack/block decisions rendering as "(no response)" — now shows
#      actual attackers/blockers from chosenArgs (eliminates false-positive annotations)
# v30: validate that all required fields are non-null strings (fixes null betterLine)
# v31: include choose_action tool spec in system prompt so annotator understands
#      mana_plan, batch combat, and other tool parameters; show mana_plan in chosen block
# v32: fix chosen=None false positives — show actual attackers/blockers/text from
#      chosenArgs instead of "?" for batch and text decisions
# v33: persist decisionIndex on annotations; export schema v8 makes it canonical
BLUNDER_SCRIPT_VERSION = 33


class BlunderAnalysisError(RuntimeError):
    """Expected operational failure during blunder annotation."""


# --- Prompt components ---

BLUNDER_EXAMPLES = """\
## Examples of Blunders

Here are some examples of the kinds of mistakes to flag:

- Not attacking for lethal, missing combo kills, burn in hand at low life
- Casting spells that accomplish nothing, cards with no valid targets, declining pure-upside abilities
- Removing the wrong threat, fetching the wrong land, naming the wrong card
- Casting spells before playing lands, creatures before combat when holding tricks
- Poor attack/block decisions, attacking into unfavorable blocks
- Missing land drops, not using mana sinks at end of opponent's turn
- Fundamentally wrong game plan decisions, not countering must-answer threats
- Overextending into board wipes, running best threat into open counter mana
- Passing priority in the postcombat main phase (with nothing on the stack) when \
there are still sorcery-speed actions available this turn — e.g. unplayed land drops, \
castable creatures or sorceries in hand, planeswalker abilities to activate. Passing \
here ends the turn and wastes those opportunities. Note: the choices list shows the \
exact legal actions available — if a player passes (Chosen: False) with playable lands \
or castable spells among the choices, that is strong evidence of a blunder."""

SHARED_SEVERITY = """\
## Severity Levels

- **questionable**: Probably suboptimal but debatable. A human reviewing the game would \
find this interesting to think about. Use this when there's at least a ~30% chance the \
play was wrong. Low bar — when in doubt, include as questionable rather than omitting.
- **minor**: Clearly suboptimal — a small amount of value was lost (e.g. slightly wrong \
sequencing, fetching a less optimal land, missing a minor advantage).
- **moderate**: A real mistake with meaningful consequences — wasted a card, missed a \
significant line, or gave the opponent an unnecessary opening.
- **major**: Game-losing or close to it — threw away a winning position, wasted multiple \
cards for nothing, missed lethal, or made an error that directly led to losing."""

ANNOTATION_SCHEMA = """\
{
  "severity": "questionable" | "minor" | "moderate" | "major",
  "description": "<what went wrong in concrete game terms>",
  "actionTaken": "<what they actually did>",
  "betterLine": "<what they should have done>"
}"""

CHOSEN_FALSE_GUIDANCE = """\
## Understanding "Chosen: False"

"Chosen: False" means the player passed priority — they declined to act. \
If the stack is empty, passing means moving to the next phase (e.g. main phase \
to combat, or postcombat main to end step — ending the turn). If the stack has \
items, passing lets those items resolve without responding.

## Understanding "Chosen: (no response)"

"Chosen: (no response)" means the player failed to respond in time (timeout) \
or their client did not send a valid action. The game engine chose a default \
for them — typically passing or skipping. Treat this like "Chosen: False" \
for blunder evaluation: if skipping was wrong given the available choices, \
flag it.

## Understanding batch/text decisions

Some decisions (attack/block declarations, color choices) use batch or text \
parameters instead of selecting from a numbered list. These show as \
"Chosen: Attack with: ...", "Chosen: Block with: ...", or "Chosen: Text: ..." \
instead of a choice name. These are valid responses — the player DID act."""


def _build_tool_reference() -> str:
    """Build a tool reference section from the MCP tool spec for choose_action."""
    mcp_tools_path = REPO_ROOT / "website" / "src" / "data" / "mcp-tools.json"
    mcp_tools = json.loads(mcp_tools_path.read_text())
    tool = next((t for t in mcp_tools if t["name"] == "choose_action"), None)
    assert tool is not None, "choose_action not found in mcp-tools.json"

    lines = [
        "## Tool Reference: choose_action",
        "",
        f"Players respond to each pending action by calling choose_action. {tool['description']}",
        "",
        "Parameters:",
    ]
    for name, schema in tool["inputSchema"]["properties"].items():
        desc = schema.get("description", "")
        type_ = schema.get("type", "")
        lines.append(f"- {name} ({type_}): {desc}")

    return "\n".join(lines)


TOOL_REFERENCE = _build_tool_reference()

PER_DECISION_SYSTEM = f"""\
You are a Magic: The Gathering expert evaluating a single decision from a game replay.

Analyze the decision below. If the play was reasonable, return null.
If it was a blunder, return a JSON annotation object.

Most decisions are reasonable — only flag clear mistakes or questionable choices.

You may be given prior context showing the board state from earlier and the action log \
since then. Use this to understand how the game reached the current state.

{BLUNDER_EXAMPLES}

{CHOSEN_FALSE_GUIDANCE}

{SHARED_SEVERITY}

## Output Format

Return ONLY valid JSON — either `null` (no blunder) or a single annotation object:
{ANNOTATION_SCHEMA}"""


_load_game = load_game


# --- Oracle text via Scryfall with disk cache ---


_extract_oracle_fields = scryfall.extract_oracle_fields
_get_oracle_texts = scryfall.get_oracle_texts


def _snapshot_zone_cards(player: SnapshotPlayer, zone: str) -> list[object]:
    """Return a snapshot player's cards for a supported public/private zone."""
    if zone == "hand":
        return player["hand"]
    if zone == "battlefield":
        return player["battlefield"]
    if zone == "graveyard":
        return player["graveyard"]
    if zone == "exile":
        return player.get("exile", [])
    if zone == "commanders":
        return player.get("commanders", [])
    raise AssertionError(f"unexpected zone {zone!r}")


def _collect_card_names(data: GameExport) -> set[str]:
    """Collect all unique card names from game snapshots and choices."""
    names: set[str] = set()
    for snap in data["snapshots"]:
        for p in snap["players"]:
            for zone in ("hand", "battlefield", "graveyard", "exile", "commanders"):
                for c in _snapshot_zone_cards(p, zone):
                    if isinstance(c, dict):
                        name = c.get("name", "")
                        if isinstance(name, str) and name:
                            names.add(name)
                    elif isinstance(c, str) and c:
                        names.add(c)
        for item in snap["stack"]:
            if isinstance(item, dict):
                name = item.get("name", "")
                if isinstance(name, str) and name:
                    names.add(name)
            elif isinstance(item, str) and item:
                names.add(item)
        for group in snap.get("combat", []):
            for a in group.get("attackers", []):
                if isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"]:
                    names.add(a["name"])
            for b in group.get("blockers", []):
                if isinstance(b, dict) and isinstance(b.get("name"), str) and b["name"]:
                    names.add(b["name"])
    # Also from choice names and combat fields in llm events
    for ev in data["llmEvents"]:
        if ev["type"] == "tool_call" and ev["tool"] == "get_action_choices":
            try:
                result = json.loads(ev["result"])
                if not isinstance(result, dict):
                    continue
                for c in result.get("choices", []):
                    if not isinstance(c, dict):
                        continue
                    name = c.get("name", "")
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
                for a in result.get("already_attacking", []):
                    if (
                        isinstance(a, dict)
                        and isinstance(a.get("name"), str)
                        and a["name"]
                    ):
                        names.add(a["name"])
                for a in result.get("incoming_attackers", []):
                    if (
                        isinstance(a, dict)
                        and isinstance(a.get("name"), str)
                        and a["name"]
                    ):
                        names.add(a["name"])
                for group in result.get("combat", []):
                    if not isinstance(group, dict):
                        continue
                    for a in group.get("attackers", []):
                        if (
                            isinstance(a, dict)
                            and isinstance(a.get("name"), str)
                            and a["name"]
                        ):
                            names.add(a["name"])
                    for b in group.get("blockers", []):
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


def _format_card_ref(card: dict) -> str:
    """Format a single card for the reference section (compact one-liner)."""
    if card.get("card_faces"):
        parts = [_format_card_ref(face).lstrip("- ") for face in card["card_faces"]]
        return "- " + " // ".join(parts)
    name = card["name"]
    mana = card.get("mana_cost", "")
    type_line = card.get("type_line", "")
    oracle = card.get("oracle_text", "")
    # Collapse newlines in oracle text to ` / ` for single-line display
    if oracle:
        oracle = oracle.replace("\n", " / ")
    pt = f" {card['power']}/{card['toughness']}" if card.get("power") else ""
    loyalty = f" [Loyalty: {card['loyalty']}]" if card.get("loyalty") else ""
    line = f"- {name} {mana} -- {type_line}{pt}{loyalty}"
    if oracle:
        line += f": {oracle}"
    return line


def _decision_game_state(decision: dict) -> dict:
    """Return a decision's game_state, asserting on malformed inputs."""
    assert "game_state" in decision, f"decision missing game_state: {decision!r}"
    game_state = decision["game_state"]
    assert isinstance(game_state, dict), (
        f"game_state must be an object, got {game_state!r}"
    )
    return game_state


def _card_names_in_decision(decision: dict) -> set[str]:
    """Extract card names referenced in a decision's game state and choices."""
    names: set[str] = set()
    gs = _decision_game_state(decision)
    for p in gs.get("players", []):
        for zone in ("hand", "battlefield", "graveyard", "exile", "commanders"):
            for c in p.get(zone, []):
                if isinstance(c, str) and c:
                    names.add(c)
                elif isinstance(c, dict) and c.get("name"):
                    names.add(c["name"])
    for item in gs.get("stack", []):
        if isinstance(item, str) and item:
            names.add(item)
        elif isinstance(item, dict) and item.get("name"):
            names.add(item["name"])
    for group in gs.get("combat", []):
        for a in group.get("attackers", []):
            if isinstance(a, dict) and a.get("name"):
                names.add(a["name"])
        for b in group.get("blockers", []):
            if isinstance(b, dict) and b.get("name"):
                names.add(b["name"])
    for c in decision.get("choices", []):
        name = c.get("name", c.get("description", ""))
        if name:
            names.add(name)
    for a in decision.get("already_attacking", []):
        if isinstance(a, dict) and a.get("name"):
            names.add(a["name"])
    for a in decision.get("incoming_attackers", []):
        if isinstance(a, dict) and a.get("name"):
            names.add(a["name"])
    for group in decision.get("combat", []):
        for a in group.get("attackers", []):
            if isinstance(a, dict) and a.get("name"):
                names.add(a["name"])
        for b in group.get("blockers", []):
            if isinstance(b, dict) and b.get("name"):
                names.add(b["name"])
    return names


_BASIC_LANDS = {
    "Plains",
    "Island",
    "Swamp",
    "Mountain",
    "Forest",
    "Snow-Covered Plains",
    "Snow-Covered Island",
    "Snow-Covered Swamp",
    "Snow-Covered Mountain",
    "Snow-Covered Forest",
    "Wastes",
}


def _card_reference_for_decision(decision: dict, oracle_texts: dict[str, dict]) -> str:
    """Build a card reference section for a single decision."""
    names = _card_names_in_decision(decision) & set(oracle_texts.keys())
    names -= _BASIC_LANDS
    if not names:
        return ""
    lines = [_format_card_ref(oracle_texts[n]) for n in sorted(names)]
    return "## Card Reference\n\n" + "\n".join(lines)


_ACTION_NOISE = re.compile(
    r" draws a card$"
    r"|^spectator\d+ has started watching$"
    r"| skip attack$"
    r"| keeps hand$"
    r"| skips Draw step$"
    r"| puts .+ from stack (onto the Battlefield|into their graveyard)$"
    r"| puts .+ from hand onto the Battlefield$"
)


def _actions_by_turn(actions: Sequence[Action]) -> dict[int, list[str]]:
    """Split action log messages into per-turn buckets using TURN markers.

    Rewrites TURN headers from XMage's sequential numbering to per-player
    turn numbers: "TURN 5 for Alice (20 - 18)" → "Alice turn 3 (20 - 18)".
    Filters out noisy/redundant messages (draw step, skip attack, zone moves).
    """
    by_turn: dict[int, list[str]] = {}
    current_turn = 0
    player_turn_counts: dict[str, int] = {}
    for a in actions:
        msg = a.get("message", "")
        assert isinstance(msg, str), f"action message must be a string, got {msg!r}"
        # Skip chat messages — LLM personality flavor adds noise and can bias
        # the blunder annotator
        if a.get("type") == "chat":
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
        if snap["turn"] == turn:
            return snap
    return None


def _format_prior_context(
    decision: dict,
    snapshots: Sequence[Snapshot],
    actions_by_turn: dict[int, list[str]],
    num_players: int,
) -> str:
    """Build prior context: snapshot from 2 turn cycles ago + action deltas.

    A turn cycle = one turn per player. So 2 cycles back = 2 * num_players
    turn numbers back from the current turn.
    """
    current_turn = decision.get("turn")
    assert isinstance(current_turn, int) or current_turn is None, (
        f"decision turn must be an int when present, got {current_turn!r}"
    )
    lookback = 2 * num_players
    if not current_turn or current_turn <= lookback:
        return ""

    ref_turn = current_turn - lookback
    ref_snap = _snapshot_for_turn(snapshots, ref_turn)
    if ref_snap is None:
        return ""

    # Format the reference snapshot using shared renderer display functions
    players_parts: list[str] = []
    for p in ref_snap["players"]:
        bf = p["battlefield"]
        s = f"{p['name']}: {p['life']}hp"
        if bf:
            s += f" bf=[{', '.join(permanent_display(x) for x in bf)}]"
        gy = p["graveyard"]
        if gy:
            s += f" gy=[{', '.join(card_display(x) for x in gy)}]"
        players_parts.append(s)

    lines = ["## Prior Context (2 turn cycles ago)\n"]
    lines.append(f"Board: {' | '.join(players_parts)}")

    # Add action deltas for turns ref_turn through current_turn - 1
    lines.append("")
    for t in range(ref_turn, current_turn):
        lines.extend(actions_by_turn.get(t, []))

    return "\n".join(lines)


def _format_current_turn_actions(
    decision: dict,
    all_actions: Sequence[Action],
    cutoff_ts: str,
) -> str:
    """Format actions from the current turn before this decision.

    Helps the LLM see what happened (or didn't happen) this turn,
    e.g. whether a land was already played or spells were cast.
    """
    current_turn = decision.get("turn")
    assert isinstance(current_turn, int) or current_turn is None, (
        f"decision turn must be an int when present, got {current_turn!r}"
    )
    if not current_turn or not cutoff_ts:
        return ""

    in_current_turn = False
    lines: list[str] = []
    for a in all_actions:
        msg = a.get("message", "")
        ts = a.get("ts", "")
        assert isinstance(msg, str), f"action message must be a string, got {msg!r}"
        assert isinstance(ts, str), (
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
        if ts >= cutoff_ts:
            break

        # Skip chat messages — LLM personality flavor adds noise
        if a.get("type") == "chat":
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


def _game_overview(data: GameExport) -> str:
    lines = [
        f"Game: {data['id']}",
        f"Format: {data['deckType']} ({data['gameType']})",
    ]
    for p in data["players"]:
        lines.append(f"  {p['name']} ({p.get('model', '?')})")
        strategy = p.get("deckStrategy")
        if strategy:
            lines.append(f"    Deck: {strategy}")
    return "\n".join(lines)


def _format_choice(c: dict) -> str:
    """Format a single choice with structured info when available."""
    raw_name = c.get("name")
    if isinstance(raw_name, str) and raw_name:
        name = raw_name
    else:
        raw_description = c.get("description")
        name = (
            raw_description
            if isinstance(raw_description, str) and raw_description
            else f"option_{c.get('index', '?')}"
        )
    extras: list[str] = []
    if c.get("id"):
        extras.append(f"id={c['id']}")
    if c.get("action"):
        extras.append(c["action"])
    if c.get("mana_cost"):
        extras.append(c["mana_cost"])
    if c.get("power") is not None and c.get("toughness") is not None:
        extras.append(f"{c['power']}/{c['toughness']}")
    if extras:
        return f"{name} ({', '.join(extras)})"
    return name


def _format_decisions(decisions: list[dict]) -> str:
    """Compact decision format for analysis."""
    parts: list[str] = []
    for d in decisions:
        if d["is_forced"]:
            continue
        gs = _decision_game_state(d)
        deciding_player = d["player"]
        players: list[str] = []
        for p in gs.get("players", []):
            bf = p.get("battlefield", [])
            lib = p.get("library_size")
            if p["name"] == deciding_player:
                # Show full hand for the deciding player
                hand = p.get("hand", [])
                if hand:
                    s = f"{p['name']}: {p.get('life', '?')}hp hand=[{', '.join(str(x) for x in hand)}]"
                else:
                    s = f"{p['name']}: {p.get('life', '?')}hp hand=0"
            else:
                # Only show public info for opponents
                s = f"{p['name']}: {p.get('life', '?')}hp"
            if lib is not None:
                s += f" lib={lib}"
            # Player counters (poison, energy, etc.)
            counters = p.get("counters")
            if counters:
                if isinstance(counters, list):
                    for ctr in counters:
                        if isinstance(ctr, dict) and ctr.get("name"):
                            s += f" {ctr['name']}={ctr.get('count', '?')}"
                elif isinstance(counters, dict):
                    for ctr_name, ctr_val in counters.items():
                        s += f" {ctr_name}={ctr_val}"
            if bf:
                s += f" bf=[{', '.join(str(x) for x in bf)}]"
            gy = p.get("graveyard", [])
            if gy:
                s += f" gy=[{', '.join(str(x) for x in gy)}]"
            exile = p.get("exile", [])
            if exile:
                s += f" exile=[{', '.join(str(x) for x in exile)}]"
            players.append(s)

        choice_descs = [_format_choice(c) for c in d.get("choices", [])]

        chosen_name = _chosen_display(d)

        stack = gs.get("stack", [])
        stack_line = ""
        if stack:
            stack_descs: list[str] = []
            for s in stack:
                if isinstance(s, str):
                    stack_descs.append(s)
                elif isinstance(s, dict):
                    desc = s.get("name", "?")
                    targets = s.get("targets", [])
                    if targets:
                        desc += " -> " + ", ".join(str(t) for t in targets)
                    stack_descs.append(desc)
                else:
                    stack_descs.append(str(s))
            stack_line = f"  Stack: [{', '.join(stack_descs)}]"

        turn = d.get("turn")
        if turn is None:
            turn = 0
        if not d.get("phase"):
            assert turn in (0, 1), (
                f"decision has empty phase on turn {turn}: {d.get('message', '')}"
            )
        phase = d.get("phase") or "PREGAME"
        lines = [
            f"[Decision {d['decision_index']}, snapshot={d['snapshot_index']}] Turn {turn} {phase} - {d['player']}",
            f"  Board: {' | '.join(players)}",
        ]
        if stack_line:
            lines.append(stack_line)
        # Combat context from game state snapshot or choices result
        combat_groups = gs.get("combat", []) or d.get("combat", [])
        if combat_groups:
            combat_parts: list[str] = []
            for group in combat_groups:
                atk_names = [
                    a["name"]
                    for a in group.get("attackers", [])
                    if isinstance(a, dict) and a.get("name")
                ]
                blk_names = [
                    b["name"]
                    for b in group.get("blockers", [])
                    if isinstance(b, dict) and b.get("name")
                ]
                part = ", ".join(atk_names)
                if blk_names:
                    part += f" blocked by {', '.join(blk_names)}"
                elif group.get("blocked"):
                    part += " (blocked)"
                if group.get("defending"):
                    part += f" -> {group['defending']}"
                combat_parts.append(part)
            lines.append(f"  Combat: {' | '.join(combat_parts)}")
        if d.get("combat_phase"):
            lines.append(f"  Combat Phase: {d['combat_phase']}")
        lines += [
            f"  Message: {d.get('message', '')}",
            f"  Choices ({len(d.get('choices', []))}): {', '.join(choice_descs)}",
            f"  Chosen: {chosen_name}",
        ]
        if "Pick triggered ability" in d.get("message", ""):
            lines.append(
                "  NOTE: This decision only determines the order triggered abilities"
                " are placed on the stack. Targets are chosen in separate decisions."
            )
        if d.get("reasoning"):
            lines.append(f"  Reasoning: {d['reasoning'][:500]}")
        # Show what the deciding player did next (but not opponent actions)
        subsequent = d.get("subsequent_actions", [])
        own_actions = [a for a in subsequent if a.startswith(deciding_player)]
        if own_actions:
            lines.append(f"  After: {'; '.join(own_actions)}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _chosen_display(d: dict) -> str:
    """Human-readable name of what was chosen in a decision.

    Delegates to the canonical renderer's _chosen_display, extracting
    the relevant fields from the decision dict.
    """
    chosen = d.get("chosen")
    chosen_args = d.get("chosenArgs") or d.get("chosen_args")
    choices = d.get("choices", [])
    return _renderer_chosen_display(chosen, chosen_args, choices)


def _compute_cost(
    prices: dict[str, tuple[float, float]],
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    price = get_model_price(model, prices)
    assert price is not None, f"No pricing found for model {model}"
    input_price, output_price = price
    return (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000


def _call_llm(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    retries: int = 3,
) -> tuple[str, int, int, int]:
    """Call LLM with retry on server errors.

    Returns (text, prompt_tokens, completion_tokens, cached_tokens).
    """
    import time

    for attempt in range(retries + 1):
        # cache_control is an OpenRouter/Anthropic vendor extension
        # not in OpenAI's type stubs — typed as Any to bypass
        system_msg: Any = {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    system_msg,
                    {"role": "user", "content": user},
                ],
                max_tokens=16384,
            )
        except OpenAIError as e:
            err_str = str(e)
            retryable = (
                "500" in err_str
                or "502" in err_str
                or "503" in err_str
                or "401" in err_str
            )
            if attempt < retries and retryable:
                print(f"    Retrying after error (attempt {attempt + 1})...")
                time.sleep(2 ** (attempt + 1))
            else:
                raise
        text = response.choices[0].message.content
        assert text is not None, "LLM returned no content"
        usage = response.usage
        assert usage is not None, "API response missing usage data"
        cached = 0
        ptd = usage.prompt_tokens_details
        if ptd is not None and ptd.cached_tokens is not None:
            cached = ptd.cached_tokens
        return text, usage.prompt_tokens, usage.completion_tokens, cached
    raise AssertionError(
        f"unreachable: loop over {retries + 1} attempts completed without return or raise"
    )


_LLM_REQUIRED_FIELDS = {"severity", "description", "actionTaken", "betterLine"}


def _parse_annotation(text: str) -> dict | None:
    """Parse a JSON annotation (object or null) from LLM response.

    Strips markdown fences if present. Returns None for null/empty responses,
    or a dict for a blunder annotation.
    """
    text = text.strip()
    # Strip markdown code fences (may appear at start or after analysis text)
    fence_match = re.search(r"```(?:json)?\s*\n", text)
    if fence_match:
        after_fence = text[fence_match.end() :]
        close = after_fence.find("```")
        text = after_fence[:close].strip() if close != -1 else after_fence.strip()

    # Check for null-like responses
    text_lower = text.lower()
    if text_lower in ("null", "[]", "none"):
        return None

    # Look for a JSON object — must start with `{"` or `{word:` (not mana like {T}, {1})
    json_match = re.search(r'\{\s*"|\{\w+\s*:', text)
    if json_match is None:
        # No JSON object — if text is analysis concluding "reasonable", treat as null
        if (
            "null" in text_lower
            or "no blunder" in text_lower
            or "reasonable" in text_lower
            or "not a blunder" in text_lower
        ):
            return None
        raise AssertionError(
            f"No JSON found and can't interpret as null:\n{text[:500]}"
        )

    start = json_match.start()
    end = text.rfind("}")
    assert end > start, f"Unmatched braces in response:\n{text[:500]}"
    json_str = text[start : end + 1]

    try:
        result = json.loads(json_str)
    except json.JSONDecodeError:
        # Fix common LLM JSON errors: unquoted keys
        fixed = re.sub(r"(?<=\{|,)\s*(\w+)\s*:", r' "\1":', json_str)
        result = json.loads(fixed)

    if result is None:
        return None
    if isinstance(result, list):
        return result[0] if result else None
    assert isinstance(result, dict), (
        f"Expected JSON object or null, got {type(result).__name__}"
    )
    return result


def _write_annotations(gz_path: str, annotations: list) -> None:
    """Write annotations (possibly empty) to the game file."""
    TMP_DIR.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, dir=str(TMP_DIR)
    ) as f:
        json.dump(annotations, f)
        ann_path = f.name

    try:
        annotate_game(gz_path, ann_path, blunder_script_version=BLUNDER_SCRIPT_VERSION)
        print(f"Annotations written to {gz_path}")
    finally:
        os.unlink(ann_path)


def _append_blunder_stats(
    *,
    game_id: str,
    decisions_analyzed: int,
    total_prompt: int,
    total_completion: int,
    total_cached: int,
    total_cost: float,
) -> None:
    """Append a run record to blunder-stats.jsonl for internals tracking."""
    from datetime import datetime

    stats_path = REPO_ROOT / "website" / "src" / "data" / "blunder-stats.jsonl"
    record = {
        "gameId": game_id,
        "ts": datetime.now(UTC).isoformat(),
        "version": BLUNDER_SCRIPT_VERSION,
        "model": OPUS_MODEL,
        "decisionsAnalyzed": decisions_analyzed,
        "promptTokens": total_prompt,
        "completionTokens": total_completion,
        "cachedTokens": total_cached,
        "costUsd": round(total_cost, 4),
    }
    with open(stats_path, "a") as f:
        f.write(json.dumps(record) + "\n")
    print(f"  Blunder stats appended to {stats_path}")


def build_decision_prompt(
    overview: str,
    decision: dict,
    oracle_texts: dict[str, dict],
    snapshots: Sequence[Snapshot],
    actions_by_turn: dict[int, list[str]],
    num_players: int,
    all_actions: Sequence[Action],
) -> tuple[str, str]:
    """Build the (system_prompt, user_message) pair for a single decision evaluation.

    Pure function with no side effects. Used by _eval_one_decision() and
    tested via golden prompt tests.

    Handles both canonical (camelCase, from export's decisions[]) and legacy
    (snake_case, from extract_decisions) decision formats.
    """
    snap_idx = snapshot_index(decision)
    snap = snapshots[snap_idx] if snap_idx < len(snapshots) else None

    if is_canonical_decision(decision):
        # Canonical format: use shared renderer
        assert snap is not None, (
            f"canonical decision references missing snapshot index {snap_idx}"
        )
        prior_ctx = _format_prior_context(
            decision, snapshots, actions_by_turn, num_players
        )
        snap_ts = snap.get("ts", "")
        turn_ctx = _format_current_turn_actions(decision, all_actions, snap_ts)
        formatted = render_decision(
            dict(decision),
            dict(snap),
            oracle_texts=oracle_texts,
            deciding_player=decision["player"],
            include_card_reference=True,
            include_chosen=True,
            prior_context=prior_ctx,
            current_turn_actions=turn_ctx,
        )
        player = decision["player"]
        user_msg = f"## Game Overview\n{overview}\n\nYou are evaluating **{player}**'s decision.\n\n{formatted}"
    else:
        # Legacy format: use old formatting code
        formatted = _format_decisions([decision])
        card_ref = _card_reference_for_decision(decision, oracle_texts)
        prior_ctx = _format_prior_context(
            decision, snapshots, actions_by_turn, num_players
        )
        snap_ts = snap.get("ts", "") if snap is not None else ""
        turn_ctx = _format_current_turn_actions(decision, all_actions, snap_ts)
        user_msg = f"## Game Overview\n{overview}"
        if card_ref:
            user_msg += f"\n\n{card_ref}"
        if prior_ctx:
            user_msg += f"\n\n{prior_ctx}"
        if turn_ctx:
            user_msg += f"\n\n{turn_ctx}"
        user_msg += f"\n\n## Decision\n\n{formatted}"

    if is_cast_rolled_back(decision):
        user_msg += (
            "\n\n**NOTE:** This cast was attempted but the game engine rolled it "
            "back because the player could not complete the mana payment. The spell "
            "never resolved — the net result was no action taken this priority window."
        )

    user_msg += f"\n\n{TOOL_REFERENCE}"

    return PER_DECISION_SYSTEM, user_msg


def _eval_one_decision(
    client: OpenAI,
    model: str,
    prices: dict[str, tuple[float, float]],
    overview: str,
    decision: dict,
    oracle_texts: dict[str, dict],
    snapshots: Sequence[Snapshot],
    actions_by_turn: dict[int, list[str]],
    num_players: int,
    all_actions: Sequence[Action],
    label: str | None = None,
) -> tuple[list[dict], float, bool, dict]:
    """Evaluate a single decision. Returns (annotations, cost_usd, parsed_ok, raw_record).

    On parse failure, prints a warning and returns ([], cost, False, raw_record).
    The raw_record contains the full prompt and response for archival.
    """
    _, user_msg = build_decision_prompt(
        overview,
        decision,
        oracle_texts,
        snapshots,
        actions_by_turn,
        num_players,
        all_actions,
    )
    if label is None:
        label = f"decision_{decision_index(decision)}"

    max_attempts = 3
    total_cost = 0.0
    text = ""
    in_tok = 0
    out_tok = 0
    cached_tok = 0
    ann: dict | None = None
    parsed_ok = True

    for attempt in range(max_attempts):
        text, in_tok, out_tok, cached_tok = _call_llm(
            client, model, PER_DECISION_SYSTEM, user_msg
        )
        attempt_cost = _compute_cost(prices, model, in_tok, out_tok)
        total_cost += attempt_cost
        suffix = f" (attempt {attempt + 1})" if attempt > 0 else ""
        cache_info = ""
        if cached_tok > 0 and in_tok > 0:
            cache_info = f" cache={cached_tok / in_tok * 100:.0f}%"
        print(
            f"  [{label}] {in_tok:,} in / {out_tok:,} out (${attempt_cost:.4f}){cache_info}{suffix}"
        )

        try:
            ann = _parse_annotation(text)
        except (json.JSONDecodeError, AssertionError) as e:
            print(f"  WARNING: Failed to parse response for {label}: {e}")
            print(f"    Raw response: {text[:200]!r}")
            if attempt < max_attempts - 1:
                continue
            parsed_ok = False
            ann = None
            break

        if ann is None:
            break

        # Validate LLM-generated fields are present and non-null strings
        missing = _LLM_REQUIRED_FIELDS - set(ann.keys())
        null_fields = {
            f for f in _LLM_REQUIRED_FIELDS if f in ann and not isinstance(ann[f], str)
        }
        if not missing and not null_fields:
            break
        bad = missing | null_fields
        print(f"  WARNING: {label} bad fields {bad}, retrying...")
        print(f"    Got: {json.dumps(ann)[:300]}")
        if attempt < max_attempts - 1:
            ann = None
        else:
            print(
                f"  WARNING: {label} still missing fields after {max_attempts} attempts, skipping"
            )
            ann = None
            break

    cost = total_cost

    d_idx = decision_index(decision)
    s_idx = snapshot_index(decision)

    raw_record = {
        "decision_index": d_idx,
        "player": decision["player"],
        "snapshot_index": s_idx,
        "model": model,
        "system_prompt": PER_DECISION_SYSTEM,
        "user_prompt": user_msg,
        "response": text,
        "prompt_tokens": in_tok,
        "completion_tokens": out_tok,
        "cached_tokens": cached_tok,
        "cost_usd": cost,
    }

    if not parsed_ok:
        return [], cost, False, raw_record

    if ann is None:
        return [], cost, True, raw_record

    # Inject constant fields the LLM doesn't need to generate.
    # snapshotIndex points to the first snapshot AFTER the action resolved,
    # so the viewer shows the annotation alongside its consequences.
    # action_seq/actionSeq is the gameSeq of the choose_action call, which
    # represents the state BEFORE the action processes.  The resulting game
    # actions get strictly higher seq values, so we need > (not >=).
    action_seq = decision.get("action_seq", 0) or decision.get("actionSeq", 0)
    action_ts = decision.get("action_ts", "")
    if action_seq:
        # v2: find first snapshot strictly after action_seq
        aftermath_idx = min(s_idx + 1, len(snapshots) - 1)
        for i in range(s_idx, len(snapshots)):
            if snapshots[i].get("seq", 0) > action_seq:
                aftermath_idx = i
                break
    elif action_ts:
        # v1: find first snapshot strictly after action_ts
        aftermath_idx = min(s_idx + 1, len(snapshots) - 1)
        for i in range(s_idx, len(snapshots)):
            if snapshots[i].get("ts", "") > action_ts:
                aftermath_idx = i
                break
    else:
        aftermath_idx = min(s_idx + 1, len(snapshots) - 1)
    ann["type"] = "blunder"
    ann["decisionIndex"] = d_idx
    ann["snapshotIndex"] = aftermath_idx
    ann["player"] = decision["player"]

    return [ann], cost, True, raw_record


def load_game_context(gz_path: str) -> dict:
    """Load and precompute all per-game context needed for eval.

    Shared by blunder_analysis.main() and blunder_eval.py.
    """
    data = _load_game(gz_path)
    decisions = extract_decisions(gz_path)
    snapshots = data.get("snapshots", [])
    overview = _game_overview(data)
    game_actions = data.get("actions", [])
    abt = _actions_by_turn(game_actions)
    num_players = len(data.get("players", []))

    card_names = _collect_card_names(data)
    oracle_texts = _get_oracle_texts(sorted(card_names))

    return {
        "data": data,
        "decisions": decisions,
        "snapshots": snapshots,
        "overview": overview,
        "oracle_texts": oracle_texts,
        "actions_by_turn": abt,
        "num_players": num_players,
        "all_actions": game_actions,
    }


def init_api() -> tuple[OpenAI, dict[str, tuple[float, float]]]:
    """Initialize OpenRouter API client and fetch pricing.

    Shared by blunder_analysis.main() and blunder_eval.py.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise BlunderAnalysisError("OPENROUTER_API_KEY environment variable required")

    prices = fetch_openrouter_prices()
    if get_model_price(OPUS_MODEL, prices) is None:
        raise BlunderAnalysisError(
            f"Could not fetch pricing for {OPUS_MODEL} from OpenRouter"
        )

    client = OpenAI(base_url=BASE_URL, api_key=api_key, timeout=300)
    return client, prices


def eval_decisions(
    decisions: list[dict],
    game_ctx: dict,
    client: OpenAI,
    prices: dict[str, tuple[float, float]],
) -> dict[int, tuple[list[dict], float, bool, dict]]:
    """Evaluate a list of decisions in parallel. Returns {decision_index: result}."""
    results_by_idx: dict[int, tuple[list[dict], float, bool, dict]] = {}

    pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    futures = {}
    for d in decisions:
        fut = pool.submit(
            _eval_one_decision,
            client,
            OPUS_MODEL,
            prices,
            game_ctx["overview"],
            d,
            game_ctx["oracle_texts"],
            game_ctx["snapshots"],
            game_ctx["actions_by_turn"],
            game_ctx["num_players"],
            game_ctx["all_actions"],
        )
        futures[fut] = decision_index(d)

    try:
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results_by_idx[idx] = fut.result()
            except OpenAIError as e:
                print(f"  WARNING: decision_{idx} failed: {e}")
                results_by_idx[idx] = ([], 0.0, False, {})
    except KeyboardInterrupt:
        print("\n  Interrupted — cancelling pending analysis...")
        for fut in futures:
            fut.cancel()
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        pool.shutdown(wait=False)

    return results_by_idx


def _auto_ingest_ground_truth(
    game_id: str,
    annotations: Sequence[Mapping[str, object]],
    decisions: Sequence[Mapping[str, object]],
    snapshots: Sequence[Mapping[str, object]],
) -> None:
    """Add annotated decisions to ground truth for future eval."""
    from scripts.analysis.blunder_eval_common import (
        make_seed_entry,
        merge_into_ground_truth,
        reverse_map_annotations,
    )

    mapping = reverse_map_annotations(annotations, decisions)

    entries: list[dict] = []
    for decision_idx in mapping.values():
        entry = make_seed_entry(decision_index(decisions[decision_idx]))
        entries.append(entry)

    if entries:
        added = merge_into_ground_truth(game_id, entries)
        if added > 0:
            print(f"Ground truth: +{added} entries for {game_id}")


def main(gz_path: str) -> float:
    # Skip if already analyzed with the current script version.
    # Missing blunderScriptVersion with existing annotations → v1.
    data = _load_game(gz_path)
    if "annotations" in data:
        existing_version = data.get("blunderScriptVersion", 1)
        if existing_version >= BLUNDER_SCRIPT_VERSION:
            print(
                f"Already analyzed (v{existing_version}): {gz_path} ({len(data['annotations'])} annotations)"
            )
            return 0.0
        print(
            f"Reanalyzing: v{existing_version} → v{BLUNDER_SCRIPT_VERSION} ({gz_path})"
        )

    client, prices = init_api()

    overview = _game_overview(data)
    print(overview)
    print()

    # Extract decisions
    decisions = extract_decisions(gz_path)

    # Build set of decision indices to skip:
    # 1. Forced decisions (only one choice)
    # 2. Failed actions (success=false, e.g. bad index/args)
    # 3. Cancelled actions (player backed out of a spell/ability)
    # 4. The cast decision that preceded a cancel (tried to cast, then undid it)
    # 5. Rolled-back decisions (intermediate mana/cost choices for a cast that
    #    failed mana payment — the initiating decision is kept with context)
    # 6. No-op decisions (pass_priority that the game ignored — no actionResult,
    #    no chosenArgs, chosen=None)
    skip_indices: set[int] = set()
    for i, d in enumerate(decisions):
        if is_forced(d):
            skip_indices.add(i)
            continue
        ar = action_result(d)
        if ar.get("success") is False:
            skip_indices.add(i)
            continue
        chosen_args = d.get("chosenArgs") or d.get("chosen_args")
        if d.get("chosen") is None and not ar and not chosen_args:
            skip_indices.add(i)
            continue
        if is_rolled_back(d):
            skip_indices.add(i)
            continue
        if is_mana_ability_subdecision(d):
            skip_indices.add(i)
            continue
        if ar.get("action_taken") == "cancelled":
            skip_indices.add(i)
            # Also skip the preceding same-player decision if it was
            # "Play spells and abilities" / "Play instants and activated abilities"
            # — the net effect was nothing (cast attempt + cancel = no action)
            for j in range(i - 1, max(i - 5, -1), -1):
                if decisions[j]["player"] != d["player"]:
                    continue
                if is_forced(decisions[j]):
                    continue
                prev_msg = decisions[j].get("message", "")
                assert isinstance(prev_msg, str), (
                    f"decision message must be a string, got {prev_msg!r}"
                )
                if prev_msg.startswith(
                    (
                        "Play spells and abilities",
                        "Play instants and activated abilities",
                    )
                ):
                    skip_indices.add(j)
                break

    non_forced = [d for i, d in enumerate(decisions) if i not in skip_indices]
    print(
        f"Extracted {len(decisions)} decisions, "
        f"skipped {len(skip_indices)} (forced/failed/cancelled/mana/noop), "
        f"{len(non_forced)} to analyze"
    )

    if not non_forced:
        print("No non-forced decisions to analyze.")
        return 0.0

    # Load game context and run parallel evaluation
    game_ctx = load_game_context(gz_path)
    print(f"Oracle texts: {len(game_ctx['oracle_texts'])} cards resolved")

    # --- Per-decision Opus analysis ---
    print(f"\nAnalyzing {len(non_forced)} decisions with {OPUS_MODEL}...")

    results_by_idx = eval_decisions(non_forced, game_ctx, client, prices)

    annotations: list[dict] = []
    raw_records: list[dict] = []
    total_cost = 0.0
    parse_failures = 0

    for d in non_forced:
        anns, cost, parsed_ok, raw = results_by_idx[decision_index(d)]
        total_cost += cost
        if not parsed_ok:
            parse_failures += 1
        annotations.extend(anns)
        if raw:
            raw_records.append(raw)

    if parse_failures > len(non_forced) / 2:
        raise BlunderAnalysisError(
            f"Too many parse failures: {parse_failures}/{len(non_forced)} decisions failed"
        )

    total_prompt = sum(r.get("prompt_tokens", 0) for r in raw_records)
    total_completion = sum(r.get("completion_tokens", 0) for r in raw_records)
    total_cached = sum(r.get("cached_tokens", 0) for r in raw_records)
    cache_pct = total_cached / total_prompt * 100 if total_prompt > 0 else 0
    print(
        f"\n  Total: {len(annotations)} annotation(s), ${total_cost:.3f}"
        f"  Cache: {total_cached:,}/{total_prompt:,} tokens ({cache_pct:.0f}%)"
    )

    # Save raw LLM data to log directory (never overwrite — new file each run)
    if raw_records:
        game_id = Path(gz_path).stem.replace(".json", "")
        log_dir = Path.home() / ".mage-bench" / "logs" / game_id
        if log_dir.is_dir():
            ts = datetime.now(_LOG_TZ).strftime("%Y%m%d_%H%M%S")
            raw_path = (
                log_dir / f"blunder_analysis_v{BLUNDER_SCRIPT_VERSION}_{ts}.jsonl"
            )
            raw_records.sort(key=lambda r: r.get("decision_index", 0))
            with open(raw_path, "w") as f:
                for rec in raw_records:
                    f.write(json.dumps(rec) + "\n")
            print(f"  Raw LLM data saved to {raw_path}")

    # Filter out annotations with invalid snapshotIndex (LLM sometimes fabricates indices)
    num_snapshots = len(data.get("snapshots", []))
    valid_annotations: list[dict] = []
    for ann in annotations:
        idx = ann.get("snapshotIndex")
        if not isinstance(idx, int) or idx < 0 or idx >= num_snapshots:
            print(
                f"  WARNING: Dropping annotation with invalid snapshotIndex {idx} (max {num_snapshots - 1})"
            )
            continue
        valid_annotations.append(ann)
    if len(valid_annotations) < len(annotations):
        print(
            f"  Dropped {len(annotations) - len(valid_annotations)} invalid annotation(s)"
        )
    annotations = valid_annotations

    if not annotations:
        print("\nNo blunders found.")
        _write_annotations(gz_path, [])
        _append_blunder_stats(
            game_id=data["id"],
            decisions_analyzed=len(non_forced),
            total_prompt=total_prompt,
            total_completion=total_completion,
            total_cached=total_cached,
            total_cost=total_cost,
        )
        print(f"\nTotal cost: ${total_cost:.3f}")
        return total_cost

    # Display blunders
    snapshots = data.get("snapshots", [])
    print(f"\nFound {len(annotations)} blunder(s):\n")
    for ann in annotations:
        snap_idx = ann["snapshotIndex"]
        turn = snapshots[snap_idx]["turn"] if snap_idx < len(snapshots) else "?"
        sev = ann["severity"].upper()
        print(f"  Turn {turn} ({ann['player']}) - {sev}")
        print(f"    {ann['description']}")
        if ann.get("betterLine"):
            print(f"    Better: {ann['betterLine']}")
        print()

    _write_annotations(gz_path, annotations)

    # Auto-ingest: add annotated decisions to ground truth for future eval
    _auto_ingest_ground_truth(data["id"], annotations, decisions, snapshots)

    # Append run stats to blunder-stats.jsonl for internals tracking
    _append_blunder_stats(
        game_id=data["id"],
        decisions_analyzed=len(non_forced),
        total_prompt=total_prompt,
        total_completion=total_completion,
        total_cached=total_cached,
        total_cost=total_cost,
    )

    print(f"\nTotal cost: ${total_cost:.3f}")
    return total_cost


def resolve_game_path(arg: str) -> str:
    """Resolve a game argument to a file path.

    Accepts either:
      - A file path (e.g. website/public/games/game_xxx.json.gz)
      - A bare game ID (e.g. game_20260225_174042_g2)
    """
    from scripts.analysis.blunder_eval_common import game_path_for_id

    p = Path(arg)
    if p.exists():
        return str(p)
    # Treat as a game ID
    return str(game_path_for_id(arg))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            f"Usage: {sys.argv[0]} <game.json.gz | game_id>",
            file=sys.stderr,
        )
        sys.exit(1)
    main(resolve_game_path(sys.argv[1]))
    from scripts.generate_leaderboard import generate_all_website_data

    generate_all_website_data()
    print("Website data regenerated", file=sys.stderr)
