#!/usr/bin/env python3
"""Analyze a game for blunders using Claude via OpenRouter.

Two-phase approach:
1. Claude Sonnet pre-filters decisions to flag suspicious ones (cheap)
2. Claude Opus analyzes only flagged decisions for detailed annotations (expensive)

Usage:
    uv run --project puppeteer python scripts/analysis/blunder_analysis.py <game.json.gz>

Requires OPENROUTER_API_KEY environment variable.
"""

import gzip
import json
import os
import random
import sys
import tempfile
from pathlib import Path

from openai import OpenAI

from annotate_game import annotate_game
from extract_decisions import extract_decisions

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TMP_DIR = REPO_ROOT / "tmp"

# Models (OpenRouter IDs from models.json)
OPUS_MODEL = "anthropic/claude-opus-4.6"
SONNET_MODEL = "anthropic/claude-sonnet-4.5"
BASE_URL = "https://openrouter.ai/api/v1"

# When the pre-filter flags zero decisions, send this many random decisions to
# Opus as a calibration check. Catches cases where the pre-filter is too conservative.
CALIBRATION_SAMPLE_SIZE = 3

# Bump this when the analysis pipeline changes enough to warrant re-running.
# Games analyzed with an older version will be automatically re-analyzed.
# v1: initial two-phase pipeline (Haiku pre-filter + Opus analysis)
# v2: softened Haiku prompt + Opus calibration check for zero-flag games
# v3: add "questionable" severity, fix Opus dismissal bias, better category examples
# v4: switch pre-filter from Haiku to Sonnet (more mechanically specific flags)
BLUNDER_SCRIPT_VERSION = 4

# Prices per million tokens (from models.json, Feb 2026)
PRICES: dict[str, tuple[float, float]] = {
    OPUS_MODEL: (5.0, 25.0),
    SONNET_MODEL: (3.0, 15.0),
}

PREFILTER_SYSTEM = """\
You are a Magic: The Gathering expert pre-filtering game decisions for blunder analysis.

Review each decision and flag any that look like potential blunders — moments where a \
clearly better line of play was available.

Common blunder patterns:
- Passing priority with playable cards and open mana (unused_mana)
- Casting spells with no meaningful impact or that hurt the caster (wasted_resources)
- Targeting the wrong permanent/player/card when a better target exists (wrong_target)
- Missing lethal damage on board (missed_lethal)
- Fundamentally wrong strategic choices (strategic_error)
- Playing cards in the wrong order — land before cantrip, creature before combat (bad_sequencing)
- Poor attack or block decisions (bad_combat)
- Overextending into obvious removal or countermagic (walked_into_removal)

Err on the side of flagging. A downstream model will confirm or dismiss each flag, \
so false positives are cheap. Only skip decisions that are clearly reasonable.

Respond with ONLY a JSON array. Each element: {"index": <decision_index>, "reason": "<brief reason>"}
If no decisions look suspicious, return []."""

OPUS_SYSTEM = """\
You are a Magic: The Gathering expert annotating blunders in a game replay.

Analyze each flagged decision carefully. Check whether the action achieved anything \
meaningful given the board state. Use the severity scale below — use "questionable" for \
borderline cases rather than omitting them. Only omit a flagged decision if the play is \
genuinely correct with no reasonable argument against it.

## Category

The "category" field is a short snake_case label you choose to describe the type of mistake. \
Use your judgment — here are some common examples, but use whatever fits best:

- `missed_lethal` — not attacking for lethal, missing combo kills, burn in hand at low life
- `wasted_resources` — casting spells that accomplish nothing, cards with no valid targets, \
countering own spells, declining pure-upside abilities
- `wrong_target` — removing the wrong threat, fetching the wrong land, naming the wrong card
- `bad_sequencing` — casting spells before playing lands, creatures before combat with tricks
- `bad_combat` — poor attack/block decisions, attacking such that opponent can make favorable blocks
- `unused_mana` — missing land drops, not using mana sinks at end of opponent's turn, \
holding castable spells for no reason
- `strategic_error` — fundamentally wrong game plan decisions, not countering must-answer threats
- `walked_into_removal` — overextending into board wipes, running best threat into open counter mana

## Severity Levels

- **questionable**: Probably suboptimal but debatable. A human reviewing the game would \
find this interesting to think about. Use this when there's at least a ~30% chance the \
play was wrong. Low bar — when in doubt, include as questionable rather than omitting.
- **minor**: Clearly suboptimal — a small amount of value was lost (e.g. slightly wrong \
sequencing, fetching a less optimal land, missing a minor advantage).
- **moderate**: A real mistake with meaningful consequences — wasted a card, missed a \
significant line, or gave the opponent an unnecessary opening.
- **major**: Game-losing or close to it — threw away a winning position, wasted multiple \
cards for nothing, missed lethal, or made an error that directly led to losing.

## Output Format

Return ONLY a JSON array of annotation objects:
{
  "snapshotIndex": <int>,
  "player": "<name>",
  "type": "blunder",
  "severity": "questionable" | "minor" | "moderate" | "major",
  "category": "<short_snake_case_label>",
  "description": "<what went wrong in concrete game terms>",
  "llmReasoning": "<why the LLM made this mistake, referencing their reasoning text>",
  "actionTaken": "<what they actually did>",
  "betterLine": "<what they should have done>"
}"""


def _load_game(gz_path: str) -> dict:
    with gzip.open(gz_path, "rt") as f:
        return json.load(f)


def _game_overview(data: dict) -> str:
    lines = [
        f"Game: {data['id']}",
        f"Format: {data.get('deckType', '?')} ({data.get('gameType', '?')})",
        f"Turns: {data['totalTurns']}",
        f"Winner: {data['winner']}",
    ]
    for p in data["players"]:
        lines.append(f"  {p['name']} ({p.get('model', '?')})")
    return "\n".join(lines)


def _format_decisions_for_prefilter(decisions: list[dict]) -> str:
    """Compact decision format for pre-filtering."""
    parts: list[str] = []
    for d in decisions:
        if d["is_forced"]:
            continue
        gs = d.get("game_state", {})
        players: list[str] = []
        for p in gs.get("players", []):
            bf = p.get("battlefield", [])
            s = f"{p['name']}: {p.get('life', '?')}hp hand={p.get('hand_count', '?')}"
            if bf:
                s += f" bf=[{', '.join(str(x) for x in bf[:8])}]"
            players.append(s)

        choice_names: list[str] = []
        for c in d.get("choices", [])[:10]:
            choice_names.append(
                c.get("name", c.get("description", f"option_{c.get('index', '?')}"))
            )

        chosen_name = _chosen_display(d)

        lines = [
            f"[Decision {d['decision_index']}] Turn {d.get('turn', '?')} "
            f"{d.get('phase', '?')} - {d['player']}",
            f"  Board: {' | '.join(players)}",
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


def _format_decisions_for_opus(
    decisions: list[dict],
    flagged: list[dict],
) -> str:
    """Full decision data for Opus, only for flagged indices."""
    flagged_map = {f["index"]: f.get("reason", "") for f in flagged}
    parts: list[str] = []
    for d in decisions:
        idx = d["decision_index"]
        if idx not in flagged_map:
            continue
        header = f"## Decision {idx} (flagged: {flagged_map[idx]})\n"
        parts.append(header + json.dumps(d, indent=2))
    return "\n\n---\n\n".join(parts)


def _format_decisions_for_opus_calibration(
    decisions: list[dict],
    sample_indices: list[int],
) -> str:
    """Full decision data for Opus calibration check (unflagged random sample)."""
    sample_set = set(sample_indices)
    parts: list[str] = []
    for d in decisions:
        idx = d["decision_index"]
        if idx not in sample_set:
            continue
        header = (
            f"## Decision {idx} (calibration sample \u2014 not flagged by pre-filter)\n"
        )
        parts.append(header + json.dumps(d, indent=2))
    return "\n\n---\n\n".join(parts)


def _compute_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    input_price, output_price = PRICES[model]
    return (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000


def _call_llm(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
) -> tuple[str, int, int]:
    """Call LLM, return (text, prompt_tokens, completion_tokens)."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=16384,
        temperature=0,
    )
    text = response.choices[0].message.content or ""
    usage = response.usage
    assert usage is not None, "API response missing usage data"
    return text, usage.prompt_tokens, usage.completion_tokens


def _parse_json_array(text: str) -> list:
    """Parse a JSON array from LLM response, stripping markdown fences if present."""
    text = text.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # drop opening fence line
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # Try extracting [...]  from surrounding text
        start = text.find("[")
        end = text.rfind("]")
        assert start != -1 and end != -1, (
            f"No JSON array found in response:\n{text[:500]}"
        )
        result = json.loads(text[start : end + 1])

    assert isinstance(result, list), f"Expected JSON array, got {type(result).__name__}"
    return result


def _print_cost(entries: list[tuple[str, str, int, int, float]]) -> None:
    total = sum(e[4] for e in entries)
    print("\nCost breakdown:")
    for label, _model, in_tok, out_tok, cost in entries:
        print(f"  {label:20s} ${cost:.3f}  ({in_tok:,} in + {out_tok:,} out)")
    print(f"  {'Total':20s} ${total:.3f}")


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

    client = OpenAI(base_url=BASE_URL, api_key=api_key)

    overview = _game_overview(data)
    print(overview)
    print()

    # Extract decisions
    decisions = extract_decisions(gz_path)
    non_forced = [d for d in decisions if not d["is_forced"]]
    print(f"Extracted {len(decisions)} decisions ({len(non_forced)} non-forced)")

    if not non_forced:
        print("No non-forced decisions to analyze.")
        return

    cost_entries: list[tuple[str, str, int, int, float]] = []

    # --- Phase 1: Sonnet pre-filter ---
    print("\nPhase 1: Pre-filtering with Sonnet...")
    prefilter_user = (
        f"## Game Overview\n{overview}\n\n"
        f"## Decisions ({len(non_forced)} non-forced)\n\n"
        f"{_format_decisions_for_prefilter(decisions)}"
    )
    pf_text, pf_in, pf_out = _call_llm(
        client, SONNET_MODEL, PREFILTER_SYSTEM, prefilter_user
    )
    pf_cost = _compute_cost(SONNET_MODEL, pf_in, pf_out)
    cost_entries.append(("Sonnet pre-filter", SONNET_MODEL, pf_in, pf_out, pf_cost))
    print(f"  Tokens: {pf_in:,} input, {pf_out:,} output (${pf_cost:.3f})")

    flagged = _parse_json_array(pf_text)
    print(f"  Flagged {len(flagged)}/{len(non_forced)} decisions")
    for f in flagged:
        print(f"    [{f['index']}] {f.get('reason', '?')}")

    if not flagged:
        # Pre-filter found nothing suspicious. Send a random sample to Opus as calibration.
        sample_size = min(CALIBRATION_SAMPLE_SIZE, len(non_forced))
        sample = random.sample(non_forced, sample_size)
        sample_indices = [d["decision_index"] for d in sample]
        print(
            f"\nNo flags from Sonnet. Calibration check: "
            f"sending {sample_size} random decisions to Opus..."
        )

        opus_user = (
            f"## Game Overview\n{overview}\n\n"
            f"## Calibration Sample\n\n"
            f"The pre-filter flagged zero decisions in this game. "
            f"Below is a random sample of {sample_size} unflagged decisions "
            f"for quality assurance. "
            f"Analyze each one. If any are blunders, provide full annotations. "
            f"If all are reasonable plays, return [].\n\n"
            f"{_format_decisions_for_opus_calibration(decisions, sample_indices)}"
        )
        opus_text, o_in, o_out = _call_llm(client, OPUS_MODEL, OPUS_SYSTEM, opus_user)
        o_cost = _compute_cost(OPUS_MODEL, o_in, o_out)
        cost_entries.append(("Opus calibration", OPUS_MODEL, o_in, o_out, o_cost))
        print(f"  Tokens: {o_in:,} input, {o_out:,} output (${o_cost:.3f})")

        annotations = _parse_json_array(opus_text)

        if not annotations:
            print("\nCalibration confirmed: no blunders in sample.")
        else:
            snapshots = data.get("snapshots", [])
            print(f"\nCalibration found {len(annotations)} blunder(s):\n")
            for ann in annotations:
                snap_idx = ann["snapshotIndex"]
                turn = snapshots[snap_idx]["turn"] if snap_idx < len(snapshots) else "?"
                sev = ann["severity"].upper()
                print(f"  Turn {turn} ({ann['player']}) - {sev} {ann['category']}")
                print(f"    {ann['description']}")
                print(f"    Better: {ann['betterLine']}")
                print()

        _write_annotations(gz_path, annotations)
        _print_cost(cost_entries)
        return

    # --- Phase 2: Opus analysis ---
    print(f"\nPhase 2: Analyzing {len(flagged)} decisions with Opus...")
    opus_user = (
        f"## Game Overview\n{overview}\n\n"
        f"## Flagged Decisions\n\n"
        f"Each decision below was pre-flagged as a potential blunder. "
        f"Analyze each one. Confirm blunders with full annotations, "
        f"omit reasonable plays.\n\n"
        f"{_format_decisions_for_opus(decisions, flagged)}"
    )
    opus_text, o_in, o_out = _call_llm(client, OPUS_MODEL, OPUS_SYSTEM, opus_user)
    o_cost = _compute_cost(OPUS_MODEL, o_in, o_out)
    cost_entries.append(("Opus analysis", OPUS_MODEL, o_in, o_out, o_cost))
    print(f"  Tokens: {o_in:,} input, {o_out:,} output (${o_cost:.3f})")

    annotations = _parse_json_array(opus_text)

    if not annotations:
        print("\nNo confirmed blunders.")
        _write_annotations(gz_path, [])
        _print_cost(cost_entries)
        return

    # Display blunders
    snapshots = data.get("snapshots", [])
    print(f"\nFound {len(annotations)} blunder(s):\n")
    for ann in annotations:
        snap_idx = ann["snapshotIndex"]
        turn = snapshots[snap_idx]["turn"] if snap_idx < len(snapshots) else "?"
        sev = ann["severity"].upper()
        print(f"  Turn {turn} ({ann['player']}) - {sev} {ann['category']}")
        print(f"    {ann['description']}")
        print(f"    Better: {ann['betterLine']}")
        print()

    _write_annotations(gz_path, annotations)

    _print_cost(cost_entries)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <game.json.gz>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
