#!/usr/bin/env python3
"""Analyze a game for blunders using Opus 4.6 via OpenRouter.

Per-decision approach: sends each non-forced decision to Opus individually
for high-quality blunder detection.

Usage:
    uv run --project puppeteer python scripts/analysis/blunder_analysis.py <game.json.gz>

Requires OPENROUTER_API_KEY environment variable.
"""

import gzip
import json
import os
import re
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openai import OpenAI

from annotate_game import annotate_game
from extract_decisions import _summarize_snapshot, extract_decisions
from puppeteer.harness_epoch import MIN_BLUNDER_VERSION  # noqa: F401 (re-exported)
from puppeteer.llm_cost import fetch_openrouter_prices, get_model_price

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TMP_DIR = REPO_ROOT / "tmp"
SCRYFALL_CACHE_PATH = Path.home() / ".mage-bench" / "scryfall-cache.json"

# Model (OpenRouter ID)
OPUS_MODEL = "anthropic/claude-opus-4.6"
BASE_URL = "https://openrouter.ai/api/v1"

# Max parallel API calls for per-decision analysis.
# OpenRouter rate limits scale with account balance ($1 = 1 RPS, max 500 RPS),
# so 50 concurrent requests is well within limits. The openai SDK retries 429s
# automatically with exponential backoff.
MAX_WORKERS = 50

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
BLUNDER_SCRIPT_VERSION = 14

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
- Overextending into board wipes, running best threat into open counter mana"""

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

PER_DECISION_SYSTEM = """\
You are a Magic: The Gathering expert evaluating a single decision from a game replay.

Analyze the decision below. If the play was reasonable, return null.
If it was a blunder, return a JSON annotation object.

Most decisions are reasonable — only flag clear mistakes or questionable choices.

You may be given prior context showing the board state from earlier and the action log \
since then. Use this to understand how the game reached the current state."""

PER_DECISION_FOOTER = f"""\
{BLUNDER_EXAMPLES}

{SHARED_SEVERITY}

## Output Format

Return ONLY valid JSON — either `null` (no blunder) or a single annotation object:
{ANNOTATION_SCHEMA}"""


def _load_game(gz_path: str) -> dict:
    with gzip.open(gz_path, "rt") as f:
        return json.load(f)


# --- Oracle text via Scryfall with disk cache ---


def _scryfall_collection(names: list[str]) -> tuple[list[dict], list[dict]]:
    """Query Scryfall /cards/collection for a batch of up to 75 names."""
    body = json.dumps({"identifiers": [{"name": n} for n in names]}).encode()
    req = urllib.request.Request(
        "https://api.scryfall.com/cards/collection",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return data.get("data", []), data.get("not_found", [])


def _extract_oracle_fields(card: dict) -> dict:
    """Extract the fields we need from a Scryfall card object."""
    fields: dict = {
        "name": card["name"],
        "mana_cost": card.get("mana_cost", ""),
        "type_line": card.get("type_line", ""),
        "oracle_text": card.get("oracle_text", ""),
    }
    if card.get("power") is not None:
        fields["power"] = card["power"]
        fields["toughness"] = card["toughness"]
    if card.get("loyalty") is not None:
        fields["loyalty"] = card["loyalty"]
    if card.get("card_faces"):
        fields["card_faces"] = [
            _extract_oracle_fields(face) for face in card["card_faces"]
        ]
    return fields


def _get_oracle_texts(names: list[str]) -> dict[str, dict]:
    """Get oracle texts for cards, using disk cache as passthrough.

    Returns {card_name: oracle_fields} for all names that resolved.
    """
    cache: dict[str, dict | None] = {}
    if SCRYFALL_CACHE_PATH.exists():
        cache = json.loads(SCRYFALL_CACHE_PATH.read_text())

    missing = [n for n in names if n not in cache]
    if missing:
        for i in range(0, len(missing), 75):
            batch = missing[i : i + 75]
            found, not_found = _scryfall_collection(batch)
            for card in found:
                cache[card["name"]] = _extract_oracle_fields(card)
            for nf in not_found:
                # Mark as not-found so we don't re-fetch next time
                cache[nf["name"]] = None
        SCRYFALL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SCRYFALL_CACHE_PATH.write_text(json.dumps(cache))
        print(f"  Scryfall: fetched {len(missing)} cards ({len(cache)} cached)")

    return {n: cache[n] for n in names if cache.get(n) is not None}


def _collect_card_names(data: dict) -> set[str]:
    """Collect all unique card names from game snapshots and choices."""
    names: set[str] = set()
    for snap in data.get("snapshots", []):
        for p in snap.get("players", []):
            for zone in ("hand", "battlefield", "graveyard", "exile", "commanders"):
                for c in p.get(zone, []):
                    if isinstance(c, dict):
                        name = c.get("name", "")
                        if name:
                            names.add(name)
                    elif isinstance(c, str) and c:
                        names.add(c)
        for item in snap.get("stack", []):
            if isinstance(item, dict):
                name = item.get("name", "")
                if name:
                    names.add(name)
            elif isinstance(item, str) and item:
                names.add(item)
    # Also from choice names in llm events
    for ev in data.get("llmEvents", []):
        if ev.get("tool") == "get_action_choices":
            try:
                result = json.loads(ev.get("result", ""))
                for c in result.get("choices", []):
                    name = c.get("name", "")
                    if name:
                        names.add(name)
            except (json.JSONDecodeError, TypeError):
                pass
    # Filter out tokens (not in Scryfall) and player names
    return {n for n in names if "Token" not in n}


def _format_card_ref(card: dict) -> str:
    """Format a single card for the reference section (compact one-liner)."""
    if card.get("card_faces"):
        parts = []
        for face in card["card_faces"]:
            # Strip leading "- " for faces since we join with " // "
            parts.append(_format_card_ref(face).lstrip("- "))
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


def _card_names_in_decision(decision: dict) -> set[str]:
    """Extract card names referenced in a decision's game state and choices."""
    names: set[str] = set()
    gs = decision.get("game_state", {})
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
    for c in decision.get("choices", []):
        name = c.get("name", c.get("description", ""))
        if name:
            names.add(name)
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


def _actions_by_turn(actions: list[dict]) -> dict[int, list[str]]:
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


def _snapshot_for_turn(snapshots: list[dict], turn: int) -> dict | None:
    """Find the first snapshot for a given turn number."""
    for snap in snapshots:
        if snap.get("turn") == turn:
            return snap
    return None


def _format_prior_context(
    decision: dict,
    snapshots: list[dict],
    actions_by_turn: dict[int, list[str]],
    num_players: int,
) -> str:
    """Build prior context: snapshot from 2 turn cycles ago + action deltas.

    A turn cycle = one turn per player. So 2 cycles back = 2 * num_players
    turn numbers back from the current turn.
    """
    current_turn = decision.get("turn")
    lookback = 2 * num_players
    if not current_turn or current_turn <= lookback:
        return ""

    ref_turn = current_turn - lookback
    ref_snap = _snapshot_for_turn(snapshots, ref_turn)
    if ref_snap is None:
        return ""

    # Format the reference snapshot
    summary = _summarize_snapshot(ref_snap)
    players_parts: list[str] = []
    for p in summary.get("players", []):
        bf = p.get("battlefield", [])
        s = f"{p['name']}: {p.get('life', '?')}hp"
        if bf:
            s += f" bf=[{', '.join(str(x) for x in bf[:8])}]"
        gy = p.get("graveyard", [])
        if gy:
            s += f" gy=[{', '.join(str(x) for x in gy)}]"
        players_parts.append(s)

    lines = ["## Prior Context (2 turn cycles ago)\n"]
    lines.append(f"Board: {' | '.join(players_parts)}")

    # Add action deltas for turns ref_turn through current_turn - 1
    lines.append("")
    for t in range(ref_turn, current_turn):
        turn_actions = actions_by_turn.get(t, [])
        for msg in turn_actions:
            lines.append(msg)

    return "\n".join(lines)


def _format_current_turn_actions(
    decision: dict,
    all_actions: list[dict],
    cutoff_ts: str,
) -> str:
    """Format actions from the current turn before this decision.

    Helps the LLM see what happened (or didn't happen) this turn,
    e.g. whether a land was already played or spells were cast.
    """
    current_turn = decision.get("turn")
    if not current_turn or not cutoff_ts:
        return ""

    in_current_turn = False
    lines: list[str] = []
    for a in all_actions:
        msg = a.get("message", "")
        ts = a.get("ts", "")

        # Track TURN markers to find current turn boundaries
        m = re.match(r"^TURN (\d+) for", msg)
        if m:
            turn_num = int(m.group(1))
            if turn_num == current_turn:
                in_current_turn = True
                continue
            elif turn_num > current_turn:
                break
            else:
                in_current_turn = False
                continue

        if not in_current_turn:
            continue

        # Only actions before the decision was presented
        if ts >= cutoff_ts:
            break

        # Filter noise (same as prior context)
        if _ACTION_NOISE.search(msg):
            continue

        if msg:
            lines.append(msg)

    if not lines:
        return "## This Turn\n(no actions yet)"

    return "## This Turn\n" + "\n".join(lines)


def _game_overview(data: dict) -> str:
    lines = [
        f"Game: {data['id']}",
        f"Format: {data.get('deckType', '?')} ({data.get('gameType', '?')})",
    ]
    for p in data["players"]:
        lines.append(f"  {p['name']} ({p.get('model', '?')})")
    return "\n".join(lines)


def _format_decisions(decisions: list[dict]) -> str:
    """Compact decision format for analysis."""
    parts: list[str] = []
    for d in decisions:
        if d["is_forced"]:
            continue
        gs = d.get("game_state", {})
        deciding_player = d["player"]
        players: list[str] = []
        for p in gs.get("players", []):
            bf = p.get("battlefield", [])
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
            if bf:
                s += f" bf=[{', '.join(str(x) for x in bf[:8])}]"
            gy = p.get("graveyard", [])
            if gy:
                s += f" gy=[{', '.join(str(x) for x in gy)}]"
            exile = p.get("exile", [])
            if exile:
                s += f" exile=[{', '.join(str(x) for x in exile)}]"
            players.append(s)

        choice_names: list[str] = []
        for c in d.get("choices", [])[:10]:
            choice_names.append(
                c.get("name", c.get("description", f"option_{c.get('index', '?')}"))
            )

        chosen_name = _chosen_display(d)

        stack = gs.get("stack", [])
        stack_line = ""
        if stack:
            stack_names = [
                s if isinstance(s, str) else s.get("name", "?") for s in stack
            ]
            stack_line = f"  Stack: [{', '.join(stack_names)}]"

        lines = [
            f"[Decision {d['decision_index']}, snapshot={d['snapshot_index']}] Turn {d.get('turn', '?')} "
            f"{d.get('phase', '?')} - {d['player']}",
            f"  Board: {' | '.join(players)}",
        ]
        if stack_line:
            lines.append(stack_line)
        lines += [
            f"  Message: {d.get('message', '')}",
            f"  Choices ({len(d.get('choices', []))}): {', '.join(choice_names)}",
            f"  Chosen: {chosen_name}",
        ]
        if d.get("reasoning"):
            lines.append(f"  Reasoning: {d['reasoning'][:500]}")
        if d.get("subsequent_actions"):
            lines.append(f"  After: {'; '.join(d['subsequent_actions'][:3])}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _chosen_display(d: dict) -> str:
    """Human-readable name of what was chosen in a decision."""
    chosen = d.get("chosen")
    choices = d.get("choices", [])
    if isinstance(chosen, bool):
        return str(chosen)
    if isinstance(chosen, int) and 0 <= chosen < len(choices):
        c = choices[chosen]
        return c.get("name", c.get("description", f"option_{chosen}"))
    if chosen is not None:
        return str(chosen)
    return "?"


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
) -> tuple[str, int, int]:
    """Call LLM with retry on server errors. Returns (text, prompt_tokens, completion_tokens)."""
    import time

    for attempt in range(retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=16384,
            )
            text = response.choices[0].message.content or ""
            usage = response.usage
            assert usage is not None, "API response missing usage data"
            return text, usage.prompt_tokens, usage.completion_tokens
        except Exception as e:
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
        if close != -1:
            text = after_fence[:close].strip()
        else:
            text = after_fence.strip()

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
        assert False, f"No JSON found and can't interpret as null:\n{text[:500]}"

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


def _eval_one_decision(
    client: OpenAI,
    model: str,
    prices: dict[str, tuple[float, float]],
    overview: str,
    decision: dict,
    oracle_texts: dict[str, dict],
    snapshots: list[dict],
    actions_by_turn: dict[int, list[str]],
    num_players: int,
    all_actions: list[dict],
) -> tuple[list[dict], float, bool, dict]:
    """Evaluate a single decision. Returns (annotations, cost_usd, parsed_ok, raw_record).

    On parse failure, prints a warning and returns ([], cost, False, raw_record).
    The raw_record contains the full prompt and response for archival.
    """
    formatted = _format_decisions([decision])
    card_ref = _card_reference_for_decision(decision, oracle_texts)
    prior_ctx = _format_prior_context(decision, snapshots, actions_by_turn, num_players)
    # Current-turn actions before this decision (shows whether land was played, etc.)
    snap_ts = snapshots[decision["snapshot_index"]].get("ts", "")
    turn_ctx = _format_current_turn_actions(decision, all_actions, snap_ts)
    user_msg = f"## Game Overview\n{overview}"
    if card_ref:
        user_msg += f"\n\n{card_ref}"
    if prior_ctx:
        user_msg += f"\n\n{prior_ctx}"
    if turn_ctx:
        user_msg += f"\n\n{turn_ctx}"
    user_msg += f"\n\n## Decision\n\n{formatted}"
    user_msg += f"\n\n{PER_DECISION_FOOTER}"
    label = f"decision_{decision['decision_index']}"

    text, in_tok, out_tok = _call_llm(client, model, PER_DECISION_SYSTEM, user_msg)
    cost = _compute_cost(prices, model, in_tok, out_tok)
    print(f"  [{label}] {in_tok:,} in / {out_tok:,} out (${cost:.4f})")

    raw_record = {
        "decision_index": decision["decision_index"],
        "player": decision["player"],
        "snapshot_index": decision["snapshot_index"],
        "model": model,
        "system_prompt": PER_DECISION_SYSTEM,
        "user_prompt": user_msg,
        "response": text,
        "prompt_tokens": in_tok,
        "completion_tokens": out_tok,
        "cost_usd": cost,
    }

    try:
        ann = _parse_annotation(text)
    except (json.JSONDecodeError, AssertionError) as e:
        print(f"  WARNING: Failed to parse response for {label}: {e}")
        print(f"    Raw response: {text[:200]!r}")
        return [], cost, False, raw_record

    if ann is None:
        return [], cost, True, raw_record

    # Inject constant fields the LLM doesn't need to generate.
    # snapshotIndex points to the first snapshot AFTER the action resolved,
    # so the viewer shows the annotation alongside its consequences.
    action_ts = decision.get("action_ts", "")
    if action_ts:
        # Find first snapshot at or after action_ts
        aftermath_idx = decision["snapshot_index"]
        for i in range(decision["snapshot_index"], len(snapshots)):
            if snapshots[i].get("ts", "") >= action_ts:
                aftermath_idx = i
                break
    else:
        aftermath_idx = decision["snapshot_index"]
    ann["type"] = "blunder"
    ann["snapshotIndex"] = aftermath_idx
    ann["player"] = decision["player"]

    return [ann], cost, True, raw_record


def main(gz_path: str) -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    assert api_key, "OPENROUTER_API_KEY environment variable required"

    # Skip if already analyzed with the current script version.
    # Missing blunderScriptVersion with existing annotations → v1.
    data = _load_game(gz_path)
    if "annotations" in data:
        existing_version = data.get("blunderScriptVersion", 1)
        if existing_version >= BLUNDER_SCRIPT_VERSION:
            print(
                f"Already analyzed (v{existing_version}): {gz_path} "
                f"({len(data['annotations'])} annotations)"
            )
            return
        print(
            f"Reanalyzing: v{existing_version} → v{BLUNDER_SCRIPT_VERSION} ({gz_path})"
        )

    # Fetch live pricing from OpenRouter
    prices = fetch_openrouter_prices()
    assert get_model_price(OPUS_MODEL, prices) is not None, (
        f"Could not fetch pricing for {OPUS_MODEL} from OpenRouter"
    )

    client = OpenAI(base_url=BASE_URL, api_key=api_key)

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
    skip_indices: set[int] = set()
    for i, d in enumerate(decisions):
        if d["is_forced"]:
            skip_indices.add(i)
            continue
        ar = d.get("action_result", {})
        if ar.get("success") is False:
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
                if decisions[j]["is_forced"]:
                    continue
                prev_msg = decisions[j].get("message", "")
                if prev_msg.startswith(
                    "Play spells and abilities"
                ) or prev_msg.startswith("Play instants and activated abilities"):
                    skip_indices.add(j)
                break

    non_forced = [d for i, d in enumerate(decisions) if i not in skip_indices]
    print(
        f"Extracted {len(decisions)} decisions, "
        f"skipped {len(skip_indices)} (forced/failed/cancelled), "
        f"{len(non_forced)} to analyze"
    )

    if not non_forced:
        print("No non-forced decisions to analyze.")
        return

    # Fetch oracle texts for all cards in the game
    card_names = _collect_card_names(data)
    oracle_texts = _get_oracle_texts(sorted(card_names))
    print(f"Oracle texts: {len(oracle_texts)} cards resolved")

    # Build action log index for prior context
    game_actions = data.get("actions", [])
    abt = _actions_by_turn(game_actions)
    game_snapshots = data.get("snapshots", [])
    num_players = len(data.get("players", []))

    # --- Per-decision Opus analysis ---
    print(f"\nAnalyzing {len(non_forced)} decisions with {OPUS_MODEL}...")

    annotations: list[dict] = []
    raw_records: list[dict] = []
    total_cost = 0.0
    parse_failures = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}
        for d in non_forced:
            fut = pool.submit(
                _eval_one_decision,
                client,
                OPUS_MODEL,
                prices,
                overview,
                d,
                oracle_texts,
                game_snapshots,
                abt,
                num_players,
                game_actions,
            )
            futures[fut] = d["decision_index"]

        # Collect results preserving decision order
        results_by_idx: dict[int, tuple[list[dict], float, bool, dict]] = {}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results_by_idx[idx] = fut.result()
            except Exception as e:
                print(f"  WARNING: decision_{idx} failed: {e}")
                results_by_idx[idx] = ([], 0.0, False, {})

    for d in non_forced:
        anns, cost, parsed_ok, raw = results_by_idx[d["decision_index"]]
        total_cost += cost
        if not parsed_ok:
            parse_failures += 1
        annotations.extend(anns)
        if raw:
            raw_records.append(raw)

    if parse_failures > len(non_forced) / 2:
        raise RuntimeError(
            f"Too many parse failures: {parse_failures}/{len(non_forced)} decisions failed"
        )

    print(f"\n  Total: {len(annotations)} annotation(s), ${total_cost:.3f}")

    # Save raw LLM data to log directory (never overwrite — new file each run)
    if raw_records:
        from datetime import datetime

        game_id = Path(gz_path).stem.replace(".json", "")
        log_dir = Path.home() / ".mage-bench" / "logs" / game_id
        if log_dir.is_dir():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
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
        print(f"\nTotal cost: ${total_cost:.3f}")
        return

    # Display blunders
    snapshots = data.get("snapshots", [])
    print(f"\nFound {len(annotations)} blunder(s):\n")
    for ann in annotations:
        snap_idx = ann["snapshotIndex"]
        turn = snapshots[snap_idx]["turn"] if snap_idx < len(snapshots) else "?"
        sev = ann["severity"].upper()
        print(f"  Turn {turn} ({ann['player']}) - {sev}")
        print(f"    {ann['description']}")
        print(f"    Better: {ann['betterLine']}")
        print()

    _write_annotations(gz_path, annotations)

    print(f"\nTotal cost: ${total_cost:.3f}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <game.json.gz>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
