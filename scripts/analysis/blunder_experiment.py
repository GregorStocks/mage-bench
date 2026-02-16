#!/usr/bin/env python3
"""Blunder analysis approach comparison experiment.

Implements multiple approaches to blunder annotation and runs them on test games
to compare quality, accuracy, and cost.

Usage:
    # Dry run: print formatted inputs without calling APIs
    uv run --project puppeteer python scripts/analysis/blunder_experiment.py --dry-run GAME.json.gz

    # Run a specific approach
    uv run --project puppeteer python scripts/analysis/blunder_experiment.py --approach baseline GAME.json.gz

    # Run all approaches on a game
    uv run --project puppeteer python scripts/analysis/blunder_experiment.py --all GAME.json.gz

    # Compare results across approaches for a game
    uv run --project puppeteer python scripts/analysis/blunder_experiment.py --compare GAME.json.gz

Requires OPENROUTER_API_KEY environment variable (except for --dry-run and --compare).
"""

import argparse
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from openai import OpenAI

from blunder_analysis import (
    OPUS_SYSTEM,
    _format_decisions,
    _game_overview,
    _load_game,
    _parse_json_array,
)
from extract_decisions import extract_decisions

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TMP_DIR = REPO_ROOT / "tmp"
RESULTS_DIR = TMP_DIR / "blunder_experiment"

BASE_URL = "https://openrouter.ai/api/v1"

# Models
OPUS = "anthropic/claude-opus-4.6"
SONNET = "anthropic/claude-sonnet-4.5"
FLASH = "google/gemini-2.5-flash"

# Prices per million tokens (from models.json)
PRICES: dict[str, tuple[float, float]] = {
    OPUS: (5.0, 25.0),
    SONNET: (3.0, 15.0),
    FLASH: (0.30, 2.50),
}


def _compute_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    input_price, output_price = PRICES[model]
    return (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000


@dataclass
class CallTrace:
    """Record of a single LLM API call."""

    model: str
    system_prompt: str
    user_prompt: str
    response_text: str
    thinking_text: str
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    cost_usd: float
    wall_time_seconds: float
    label: str = ""  # e.g. "decision_16" or "full_game"

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "label": self.label,
            "system_prompt": self.system_prompt,
            "user_prompt": self.user_prompt,
            "response_text": self.response_text,
            "thinking_text": self.thinking_text,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens,
            "cost_usd": self.cost_usd,
            "wall_time_seconds": self.wall_time_seconds,
        }


@dataclass
class ExperimentResult:
    approach: str
    game_id: str
    model: str
    annotations: list[dict] = field(default_factory=list)
    calls: list[CallTrace] = field(default_factory=list)

    @property
    def input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    @property
    def thinking_tokens(self) -> int:
        return sum(c.thinking_tokens for c in self.calls)

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def num_api_calls(self) -> int:
        return len(self.calls)

    @property
    def wall_time_seconds(self) -> float:
        return sum(c.wall_time_seconds for c in self.calls)

    def to_dict(self) -> dict:
        return {
            "approach": self.approach,
            "game_id": self.game_id,
            "model": self.model,
            "annotations": self.annotations,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens,
            "cost_usd": self.cost_usd,
            "num_api_calls": self.num_api_calls,
            "wall_time_seconds": self.wall_time_seconds,
            "calls": [c.to_dict() for c in self.calls],
        }


def _call_llm(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    thinking: bool = False,
    label: str = "",
) -> CallTrace:
    """Call LLM and return a full CallTrace with all intermediate data."""
    kwargs: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 16384,
    }

    if thinking:
        # OpenRouter extended thinking via extra_body
        kwargs["extra_body"] = {"reasoning": {"effort": "high"}}
        # Can't use temperature with thinking mode on some models
    else:
        kwargs["temperature"] = 0

    start = time.monotonic()
    response = client.chat.completions.create(**kwargs)
    elapsed = time.monotonic() - start

    text = response.choices[0].message.content or ""
    usage = response.usage
    assert usage is not None, "API response missing usage data"

    # Extract thinking tokens and text if present
    thinking_tokens = 0
    if hasattr(usage, "completion_tokens_details") and usage.completion_tokens_details:
        details = usage.completion_tokens_details
        thinking_tokens = getattr(details, "reasoning_tokens", 0) or 0

    thinking_text = ""
    choice = response.choices[0]
    if hasattr(choice.message, "reasoning_content") and choice.message.reasoning_content:
        thinking_text = choice.message.reasoning_content
    elif hasattr(choice.message, "reasoning") and choice.message.reasoning:
        thinking_text = choice.message.reasoning

    cost = _compute_cost(model, usage.prompt_tokens, usage.completion_tokens)

    trace = CallTrace(
        model=model,
        system_prompt=system,
        user_prompt=user,
        response_text=text,
        thinking_text=thinking_text,
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        thinking_tokens=thinking_tokens,
        cost_usd=cost,
        wall_time_seconds=elapsed,
        label=label,
    )

    # Print per-call cost info
    think_str = f", {thinking_tokens:,} thinking" if thinking_tokens else ""
    print(
        f"    [{label or 'call'}] {model} "
        f"{usage.prompt_tokens:,} in / {usage.completion_tokens:,} out{think_str} "
        f"${cost:.4f} ({elapsed:.1f}s)"
    )

    return trace


# --- Shared prompt components ---

# Categories and severity (shared across all approaches)
SHARED_CATEGORIES = """\
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
- `walked_into_removal` — overextending into board wipes, running best threat into open counter mana"""

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


# --- Approach A: Inline annotation ---

INLINE_SYSTEM = f"""\
You are a Magic: The Gathering expert annotating blunders in a game replay.

You will see decisions one at a time. For EACH decision, you must respond with EXACTLY \
one of these two formats:

1. If the decision is reasonable: respond with just the word PASS on its own line.
2. If the decision is a blunder: respond with a JSON annotation object.

Process decisions IN ORDER. Respond to each decision IMMEDIATELY before reading the next one. \
Do NOT skip ahead or batch your responses.

{SHARED_CATEGORIES}

{SHARED_SEVERITY}

## Output Format

For each decision, output either PASS or a JSON annotation object:
{ANNOTATION_SCHEMA}

Use the snapshot= number from the decision header as snapshotIndex."""


def _approach_inline(
    client: OpenAI, data: dict, decisions: list[dict], overview: str
) -> ExperimentResult:
    """Approach A: Inline annotation — one Opus call, annotate each decision as you go."""
    result = ExperimentResult(approach="A_inline", game_id=data["id"], model=OPUS)
    non_forced = [d for d in decisions if not d["is_forced"]]

    # Format each decision with a clear separator
    parts: list[str] = []
    for d in non_forced:
        formatted = _format_decisions([d])
        parts.append(
            f"--- DECISION (respond PASS or annotation below) ---\n{formatted}"
        )

    user_msg = (
        f"## Game Overview\n{overview}\n\n"
        f"## Decisions ({len(non_forced)} total)\n\n" + "\n\n".join(parts)
    )

    trace = _call_llm(client, OPUS, INLINE_SYSTEM, user_msg, label="full_game")
    result.calls.append(trace)

    # Parse interleaved PASS / JSON responses
    result.annotations = _parse_inline_response(trace.response_text)
    return result


def _parse_inline_response(text: str) -> list[dict]:
    """Parse interleaved PASS / JSON annotation blocks from inline approach output."""
    annotations: list[dict] = []
    # Find all JSON objects in the text (between { and })
    # We use a simple brace-matching approach
    i = 0
    while i < len(text):
        if text[i] == "{":
            # Find matching closing brace
            depth = 0
            start = i
            while i < len(text):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(text[start : i + 1])
                            if isinstance(obj, dict) and "snapshotIndex" in obj:
                                annotations.append(obj)
                        except json.JSONDecodeError:
                            pass
                        break
                i += 1
        i += 1
    return annotations


# --- Approach B/D/E: Per-decision (parameterized by model) ---

PER_DECISION_SYSTEM = f"""\
You are a Magic: The Gathering expert evaluating a single decision from a game replay.

Analyze the decision below. If the play was reasonable, return an empty JSON array: []
If it was a blunder, return a JSON array with one annotation object.

Most decisions are reasonable — only flag clear mistakes or questionable choices.

{SHARED_CATEGORIES}

{SHARED_SEVERITY}

## Output Format

Return ONLY a JSON array — either empty [] or containing one annotation object:
{ANNOTATION_SCHEMA}

Use the snapshot= number from the decision header as snapshotIndex."""


def _approach_per_decision(
    client: OpenAI,
    data: dict,
    decisions: list[dict],
    overview: str,
    model: str,
    approach_name: str,
) -> ExperimentResult:
    """Per-decision approach: one API call per non-forced decision."""
    result = ExperimentResult(approach=approach_name, game_id=data["id"], model=model)
    non_forced = [d for d in decisions if not d["is_forced"]]

    start = time.monotonic()
    for d in non_forced:
        formatted = _format_decisions([d])
        user_msg = f"## Game Overview\n{overview}\n\n## Decision\n\n{formatted}"

        text, in_tok, out_tok, think_tok = _call_llm(
            client, model, PER_DECISION_SYSTEM, user_msg
        )
        result.input_tokens += in_tok
        result.output_tokens += out_tok
        result.thinking_tokens += think_tok
        result.cost_usd += _compute_cost(model, in_tok, out_tok)
        result.num_api_calls += 1

        try:
            anns = _parse_json_array(text)
            result.annotations.extend(anns)
        except (json.JSONDecodeError, AssertionError):
            print(
                f"    WARNING: Failed to parse response for decision {d['decision_index']}"
            )

    result.wall_time_seconds = time.monotonic() - start
    return result


# --- Approach C: Extended thinking on Opus ---


def _approach_thinking(
    client: OpenAI, data: dict, decisions: list[dict], overview: str
) -> ExperimentResult:
    """Approach C: Current single-pass with extended thinking enabled."""
    result = ExperimentResult(approach="C_thinking", game_id=data["id"], model=OPUS)
    non_forced = [d for d in decisions if not d["is_forced"]]

    user_msg = (
        f"## Game Overview\n{overview}\n\n"
        f"## Decisions ({len(non_forced)} non-forced)\n\n"
        f"{_format_decisions(decisions)}"
    )

    start = time.monotonic()
    text, in_tok, out_tok, think_tok = _call_llm(
        client, OPUS, OPUS_SYSTEM, user_msg, thinking=True
    )
    result.wall_time_seconds = time.monotonic() - start
    result.input_tokens = in_tok
    result.output_tokens = out_tok
    result.thinking_tokens = think_tok
    result.cost_usd = _compute_cost(OPUS, in_tok, out_tok)
    result.num_api_calls = 1

    try:
        result.annotations = _parse_json_array(text)
    except (json.JSONDecodeError, AssertionError) as e:
        print(f"    WARNING: Failed to parse thinking response: {e}")

    return result


# --- Baseline: Current v5 ---


def _approach_baseline(
    client: OpenAI, data: dict, decisions: list[dict], overview: str
) -> ExperimentResult:
    """Baseline: Current single-pass Opus without thinking (v5 logic)."""
    result = ExperimentResult(approach="baseline", game_id=data["id"], model=OPUS)
    non_forced = [d for d in decisions if not d["is_forced"]]

    user_msg = (
        f"## Game Overview\n{overview}\n\n"
        f"## Decisions ({len(non_forced)} non-forced)\n\n"
        f"{_format_decisions(decisions)}"
    )

    start = time.monotonic()
    text, in_tok, out_tok, think_tok = _call_llm(client, OPUS, OPUS_SYSTEM, user_msg)
    result.wall_time_seconds = time.monotonic() - start
    result.input_tokens = in_tok
    result.output_tokens = out_tok
    result.thinking_tokens = think_tok
    result.cost_usd = _compute_cost(OPUS, in_tok, out_tok)
    result.num_api_calls = 1

    try:
        result.annotations = _parse_json_array(text)
    except (json.JSONDecodeError, AssertionError) as e:
        print(f"    WARNING: Failed to parse baseline response: {e}")

    return result


# --- Registry ---

APPROACHES: dict[str, tuple[str, object]] = {
    "baseline": ("Current v5 (single Opus, no thinking)", _approach_baseline),
    "A_inline": (
        "Inline annotation (single Opus, annotate-as-you-go)",
        None,
    ),  # special
    "B_flash": ("Per-decision Gemini 2.5 Flash", None),  # special
    "C_thinking": ("Extended thinking Opus (single pass)", _approach_thinking),
    "D_opus": ("Per-decision Opus", None),  # special
    "E_sonnet": ("Per-decision Sonnet 4.5", None),  # special
}


def run_approach(
    approach: str, client: OpenAI, data: dict, decisions: list[dict], overview: str
) -> ExperimentResult:
    """Run a specific approach and return the result."""
    if approach == "baseline":
        return _approach_baseline(client, data, decisions, overview)
    elif approach == "A_inline":
        return _approach_inline(client, data, decisions, overview)
    elif approach == "B_flash":
        return _approach_per_decision(
            client, data, decisions, overview, FLASH, "B_flash"
        )
    elif approach == "C_thinking":
        return _approach_thinking(client, data, decisions, overview)
    elif approach == "D_opus":
        return _approach_per_decision(client, data, decisions, overview, OPUS, "D_opus")
    elif approach == "E_sonnet":
        return _approach_per_decision(
            client, data, decisions, overview, SONNET, "E_sonnet"
        )
    else:
        raise ValueError(f"Unknown approach: {approach}")


def _save_result(result: ExperimentResult) -> Path:
    """Save experiment result to disk."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{result.game_id}_{result.approach}.json"
    with open(path, "w") as f:
        json.dump(result.to_dict(), f, indent=2)
    return path


def _load_results(game_id: str) -> list[dict]:
    """Load all experiment results for a game."""
    results = []
    if not RESULTS_DIR.exists():
        return results
    for p in sorted(RESULTS_DIR.glob(f"{game_id}_*.json")):
        with open(p) as f:
            results.append(json.load(f))
    return results


def _print_comparison(game_id: str, results: list[dict], data: dict) -> None:
    """Print a comparison table of approach results."""
    num_snapshots = len(data.get("snapshots", []))

    print(f"\n{'=' * 80}")
    print(f"Comparison for {game_id}")
    print(f"{'=' * 80}")
    print(
        f"{'Approach':<20} {'Anns':>4} {'Major':>5} {'Mod':>5} {'Minor':>5} "
        f"{'Quest':>5} {'Calls':>5} {'In tok':>8} {'Out tok':>8} {'Think':>8} "
        f"{'Cost':>7} {'Time':>6}"
    )
    print("-" * 110)

    for r in results:
        anns = r["annotations"]
        sev_counts = {"major": 0, "moderate": 0, "minor": 0, "questionable": 0}
        for a in anns:
            sev = a.get("severity", "")
            if sev in sev_counts:
                sev_counts[sev] += 1

        print(
            f"{r['approach']:<20} {len(anns):>4} {sev_counts['major']:>5} "
            f"{sev_counts['moderate']:>5} {sev_counts['minor']:>5} "
            f"{sev_counts['questionable']:>5} {r['num_api_calls']:>5} "
            f"{r['input_tokens']:>8,} {r['output_tokens']:>8,} "
            f"{r['thinking_tokens']:>8,} "
            f"${r['cost_usd']:>6.3f} {r['wall_time_seconds']:>5.1f}s"
        )

    # Show annotation details
    for r in results:
        anns = r["annotations"]
        if not anns:
            continue
        print(f"\n--- {r['approach']} annotations ---")
        for a in anns:
            snap = a.get("snapshotIndex", "?")
            valid = (
                "OK" if isinstance(snap, int) and 0 <= snap < num_snapshots else "BAD"
            )
            print(
                f"  [{valid}] snap={snap} {a.get('player', '?')} "
                f"{a.get('severity', '?').upper()} {a.get('category', '?')}"
            )
            print(f"       {a.get('description', '')[:120]}")


def _dry_run(gz_path: str) -> None:
    """Print formatted inputs without calling APIs."""
    data = _load_game(gz_path)
    decisions = extract_decisions(gz_path)
    non_forced = [d for d in decisions if not d["is_forced"]]
    overview = _game_overview(data)

    print(f"Game: {data['id']}")
    print(f"Snapshots: {len(data.get('snapshots', []))}")
    print(f"Decisions: {len(decisions)} total, {len(non_forced)} non-forced")
    print()

    # Show what baseline/thinking would send
    all_formatted = _format_decisions(decisions)
    baseline_user = (
        f"## Game Overview\n{overview}\n\n"
        f"## Decisions ({len(non_forced)} non-forced)\n\n"
        f"{all_formatted}"
    )
    print("=== Baseline/Thinking input ===")
    print(f"System: {len(OPUS_SYSTEM)} chars (~{len(OPUS_SYSTEM) // 4} tokens)")
    print(f"User: {len(baseline_user)} chars (~{len(baseline_user) // 4} tokens)")
    print()

    # Show what per-decision would send for one decision
    if non_forced:
        d = non_forced[0]
        formatted = _format_decisions([d])
        per_dec_user = f"## Game Overview\n{overview}\n\n## Decision\n\n{formatted}"
        print(f"=== Per-decision input (decision {d['decision_index']}) ===")
        print(
            f"System: {len(PER_DECISION_SYSTEM)} chars (~{len(PER_DECISION_SYSTEM) // 4} tokens)"
        )
        print(f"User: {len(per_dec_user)} chars (~{len(per_dec_user) // 4} tokens)")
        print()

    # Show inline approach input
    parts = []
    for d in non_forced:
        formatted = _format_decisions([d])
        parts.append(
            f"--- DECISION (respond PASS or annotation below) ---\n{formatted}"
        )
    inline_user = (
        f"## Game Overview\n{overview}\n\n"
        f"## Decisions ({len(non_forced)} total)\n\n" + "\n\n".join(parts)
    )
    print("=== Inline input ===")
    print(f"System: {len(INLINE_SYSTEM)} chars (~{len(INLINE_SYSTEM) // 4} tokens)")
    print(f"User: {len(inline_user)} chars (~{len(inline_user) // 4} tokens)")
    print()

    # Cost estimates
    print("=== Estimated costs ===")
    sys_base = len(OPUS_SYSTEM) // 4
    sys_per = len(PER_DECISION_SYSTEM) // 4
    sys_inline = len(INLINE_SYSTEM) // 4
    overview_tok = len(overview) // 4

    baseline_in = sys_base + len(baseline_user) // 4
    print(f"Baseline (Opus):     ~${_compute_cost(OPUS, baseline_in, 500):.3f}")
    print(
        f"Thinking (Opus):     ~${_compute_cost(OPUS, baseline_in, 2000):.3f} (more output from thinking)"
    )
    print(
        f"Inline (Opus):       ~${_compute_cost(OPUS, sys_inline + len(inline_user) // 4, 800):.3f}"
    )

    per_dec_in = sum(
        sys_per + overview_tok + len(_format_decisions([d])) // 4 for d in non_forced
    )
    per_dec_out = len(non_forced) * 80
    print(
        f"Per-decision (Opus): ~${_compute_cost(OPUS, per_dec_in, per_dec_out):.3f} ({len(non_forced)} calls)"
    )
    print(
        f"Per-decision (Son):  ~${_compute_cost(SONNET, per_dec_in, per_dec_out):.3f} ({len(non_forced)} calls)"
    )
    print(
        f"Per-decision (Flash):~${_compute_cost(FLASH, per_dec_in, per_dec_out):.3f} ({len(non_forced)} calls)"
    )

    # Show existing annotations for comparison
    existing = data.get("annotations", [])
    if existing:
        print(f"\n=== Existing v5 annotations ({len(existing)}) ===")
        for a in existing:
            print(
                f"  snap={a.get('snapshotIndex', '?')} {a.get('player', '?')} "
                f"{a.get('severity', '?').upper()} {a.get('category', '?')}"
            )
            print(f"       {a.get('description', '')[:120]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Blunder analysis experiment")
    parser.add_argument("game", help="Path to game .json.gz file")
    parser.add_argument(
        "--approach", choices=list(APPROACHES.keys()), help="Run specific approach"
    )
    parser.add_argument("--all", action="store_true", help="Run all approaches")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print inputs without calling APIs"
    )
    parser.add_argument(
        "--compare", action="store_true", help="Compare existing results"
    )
    args = parser.parse_args()

    if args.dry_run:
        _dry_run(args.game)
        return

    data = _load_game(args.game)
    game_id = data["id"]

    if args.compare:
        results = _load_results(game_id)
        if not results:
            print(f"No results found for {game_id}")
            return
        _print_comparison(game_id, results, data)
        return

    api_key = os.environ.get("OPENROUTER_API_KEY")
    assert api_key, "OPENROUTER_API_KEY environment variable required"

    client = OpenAI(base_url=BASE_URL, api_key=api_key)
    decisions = extract_decisions(args.game)
    overview = _game_overview(data)
    non_forced = [d for d in decisions if not d["is_forced"]]

    print(f"Game: {game_id}")
    print(f"Decisions: {len(decisions)} total, {len(non_forced)} non-forced")
    print()

    approaches_to_run: list[str] = []
    if args.all:
        approaches_to_run = list(APPROACHES.keys())
    elif args.approach:
        approaches_to_run = [args.approach]
    else:
        parser.error("Specify --approach, --all, --dry-run, or --compare")

    for approach in approaches_to_run:
        desc = APPROACHES[approach][0]
        print(f"Running {approach}: {desc}...")
        result = run_approach(approach, client, data, decisions, overview)
        path = _save_result(result)
        print(
            f"  {len(result.annotations)} annotations, "
            f"{result.num_api_calls} calls, "
            f"${result.cost_usd:.3f}, "
            f"{result.wall_time_seconds:.1f}s"
        )
        print(f"  Saved to {path}")
        print()

    # Show comparison if we ran multiple
    if len(approaches_to_run) > 1:
        results = _load_results(game_id)
        _print_comparison(game_id, results, data)


if __name__ == "__main__":
    main()
