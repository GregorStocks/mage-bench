#!/usr/bin/env python3
"""Analyze a game for blunders using Claude Opus via OpenRouter.

Single-phase approach: sends all non-forced decisions to Opus in one pass.
Opus identifies and annotates blunders directly, skipping reasonable plays.

Usage:
    uv run --project puppeteer python scripts/analysis/blunder_analysis.py <game.json.gz>

Requires OPENROUTER_API_KEY environment variable.
"""

import gzip
import json
import os
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
BASE_URL = "https://openrouter.ai/api/v1"

# Bump this when the analysis pipeline changes enough to warrant re-running.
# Games analyzed with an older version will be automatically re-analyzed.
# v1: initial two-phase pipeline (Haiku pre-filter + Opus analysis)
# v2: softened Haiku prompt + Opus calibration check for zero-flag games
# v3: add "questionable" severity, fix Opus dismissal bias, better category examples
# v4: switch pre-filter from Haiku to Sonnet (more mechanically specific flags)
# v5: single-phase Opus (no pre-filter, cheaper, better coverage, 1M context)
BLUNDER_SCRIPT_VERSION = 5

# Prices per million tokens (from models.json, Feb 2026)
PRICES: dict[str, tuple[float, float]] = {
    OPUS_MODEL: (5.0, 25.0),
}

OPUS_SYSTEM = """\
You are a Magic: The Gathering expert annotating blunders in a game replay.

Review ALL decisions below. Most will be reasonable plays — skip those. Flag any \
decision where the player made a clear mistake or a questionable choice. Use the \
severity scale below — use "questionable" for borderline cases. Only annotate \
decisions where there's a real argument the play was wrong.

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
- `strategic_error` — fundamentally wrong game plan decisions, not countering must-answer threats, \
choosing to go second
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

Return ONLY a JSON array of annotation objects. Use the snapshot= number from the \
decision header as snapshotIndex (NOT the decision number):
{
  "snapshotIndex": <int from snapshot= in decision header>,
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


def _format_decisions(decisions: list[dict]) -> str:
    """Compact decision format for Opus analysis."""
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
            f"[Decision {d['decision_index']}, snapshot={d['snapshot_index']}] Turn {d.get('turn', '?')} "
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

    # --- Single-phase Opus analysis ---
    print("\nAnalyzing with Opus...")
    user_msg = (
        f"## Game Overview\n{overview}\n\n"
        f"## Decisions ({len(non_forced)} non-forced)\n\n"
        f"{_format_decisions(decisions)}"
    )
    total_cost = 0.0
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        text, in_tok, out_tok = _call_llm(client, OPUS_MODEL, OPUS_SYSTEM, user_msg)
        cost = _compute_cost(OPUS_MODEL, in_tok, out_tok)
        total_cost += cost
        print(f"  Tokens: {in_tok:,} input, {out_tok:,} output (${cost:.3f})")

        try:
            annotations = _parse_json_array(text)
            break
        except (json.JSONDecodeError, AssertionError) as e:
            # Log the bad response for debugging
            TMP_DIR.mkdir(exist_ok=True)
            dump_path = TMP_DIR / f"bad_blunder_response_attempt{attempt}.txt"
            dump_path.write_text(text)
            if attempt < max_attempts:
                print(
                    f"  Opus returned invalid JSON (attempt {attempt}/{max_attempts}): {e}\n"
                    f"  Response dumped to {dump_path}\n"
                    f"  Retrying..."
                )
            else:
                raise RuntimeError(
                    f"Opus returned invalid JSON on all {max_attempts} attempts. "
                    f"Last error: {e}\n"
                    f"Last response dumped to {dump_path}"
                ) from e

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
        print(f"  Turn {turn} ({ann['player']}) - {sev} {ann['category']}")
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
