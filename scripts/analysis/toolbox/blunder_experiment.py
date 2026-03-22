#!/usr/bin/env python3
"""Blunder analysis approach comparison experiment.

HISTORICAL ARTIFACT: This script was used to compare different approaches (A-Q)
for blunder annotation. The winner (approach P: per-decision Sonnet 4.5 + low
thinking) is now the production pipeline in blunder_analysis.py. We don't intend
to do further experiments at this time.

Implements multiple approaches to blunder annotation and runs them on test games
to compare quality, accuracy, and cost.

Usage:
    # Dry run: print formatted inputs without calling APIs
    uv run --project puppeteer python scripts/analysis/toolbox/blunder_experiment.py --dry-run GAME.json.gz

    # Run a specific approach
    uv run --project puppeteer python scripts/analysis/toolbox/blunder_experiment.py --approach baseline GAME.json.gz

    # Run all approaches on a game
    uv run --project puppeteer python scripts/analysis/toolbox/blunder_experiment.py --all GAME.json.gz

    # Compare results across approaches for a game
    uv run --project puppeteer python scripts/analysis/toolbox/blunder_experiment.py --compare GAME.json.gz

Requires OPENROUTER_API_KEY environment variable (except for --dry-run and --compare).
"""

import argparse
import json
import os
import time
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias

from openai import OpenAI

from puppeteer.decision_renderer import (
    chosen_display,
    format_choice,
)
from magebench.game.game_export_types import BuiltGameExport, Decision, GameExport
from scripts.analysis.blunder_context import game_overview
from scripts.analysis.blunder_eval_common import (
    decision_index,
    is_forced,
    load_game_for_annotation,
)
from scripts.analysis.extract_decisions import extract_decisions

GameData: TypeAlias = BuiltGameExport | GameExport


def _parse_json_array(text: str) -> list:
    """Parse a JSON array from LLM response, stripping markdown fences if present."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        assert start != -1 and end != -1, (
            f"No JSON array found in response:\n{text[:500]}"
        )
        result = json.loads(text[start : end + 1])
    assert isinstance(result, list), f"Expected JSON array, got {type(result).__name__}"
    return result


# Legacy single-pass Opus system prompt — used only by baseline and thinking approaches.
# Removed from production blunder_analysis.py in v6 (switched to per-decision approach).
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

Return ONLY a JSON array of annotation objects. Use the Decision number from the \
decision header as decisionIndex:
{
  "decisionIndex": <int from Decision number in decision header>,
  "player": "<name>",
  "type": "blunder",
  "severity": "questionable" | "minor" | "moderate" | "major",
  "category": "<short_snake_case_label>",
  "description": "<what went wrong in concrete game terms>",
  "llmReasoning": "<why the LLM made this mistake, referencing their reasoning text>",
  "actionTaken": "<what they actually did>",
  "betterLine": "<what they should have done>"
}"""

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
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

# Max parallel API calls for per-decision approaches
MAX_WORKERS = 8


def _format_decisions(decisions: Sequence[Decision]) -> str:
    """Compact local formatter for the historical experiment prompts.

    Keep this formatter in the experiment module so the live annotator no longer
    needs to carry dead private helpers just for old toolbox scripts.
    """
    parts: list[str] = []
    for decision in decisions:
        if is_forced(decision):
            continue
        phase = decision.phase or "PREGAME"
        choices = ", ".join(format_choice(choice) for choice in decision.choices)
        chosen = chosen_display(decision.chosen, decision.chosen_args, decision.choices)
        lines = [
            (
                f"[Decision {decision.index}, snapshot={decision.snapshot_index}] "
                f"Turn {decision.turn} {phase} - {decision.player}"
            ),
            f"  Message: {decision.message}" if decision.message else "  Message:",
            f"  Choices ({len(decision.choices)}): {choices}",
            f"  Chosen: {chosen}",
        ]
        own_actions = [
            action
            for action in decision.subsequent_actions
            if action.startswith(decision.player)
        ]
        if own_actions:
            lines.append(f"  After: {'; '.join(own_actions)}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


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
    reasoning_effort: str | None = None,
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

    if reasoning_effort is not None:
        # OpenRouter extended thinking via extra_body
        kwargs["extra_body"] = {"reasoning": {"effort": reasoning_effort}}
        # Can't use temperature with thinking mode on some models
    else:
        kwargs["temperature"] = 0

    start = time.monotonic()
    response = client.chat.completions.create(**kwargs)
    elapsed = time.monotonic() - start

    text = response.choices[0].message.content
    assert text is not None, "LLM returned no content"
    usage = response.usage
    assert usage is not None, "API response missing usage data"

    # Extract thinking tokens and text if present
    thinking_tokens = 0
    if hasattr(usage, "completion_tokens_details") and usage.completion_tokens_details:
        details = usage.completion_tokens_details
        if hasattr(details, "reasoning_tokens"):
            raw_reasoning_tokens = details.reasoning_tokens
            assert raw_reasoning_tokens is None or isinstance(
                raw_reasoning_tokens, int
            ), (
                f"reasoning_tokens must be an int when present, got {raw_reasoning_tokens!r}"
            )
            thinking_tokens = raw_reasoning_tokens or 0

    thinking_text = ""
    choice = response.choices[0]
    if (
        hasattr(choice.message, "reasoning_content")
        and choice.message.reasoning_content
    ):
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
  "decisionIndex": <int>,
  "player": "<name>",
  "type": "blunder",
  "severity": "questionable" | "minor" | "moderate" | "major",
  "category": "<short_snake_case_label>",
  "description": "<what went wrong in concrete game terms>",
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

Use the Decision number from the decision header as decisionIndex."""


def _approach_inline(
    client: OpenAI,
    data: GameData,
    decisions: list[Decision],
    overview: str,
) -> ExperimentResult:
    """Approach A: Inline annotation — one Opus call, annotate each decision as you go."""
    result = ExperimentResult(approach="A_inline", game_id=data.id, model=OPUS)
    non_forced = [d for d in decisions if not is_forced(d)]

    # Format each decision with a clear separator
    parts: list[str] = []
    for d in non_forced:
        formatted = _format_decisions([d])
        parts.append(
            f"--- DECISION (respond PASS or annotation below) ---\n{formatted}"
        )

    user_msg = (
        f"## Game Overview\n{overview}\n\n## Decisions ({len(non_forced)} total)\n\n"
        + "\n\n".join(parts)
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
                            if isinstance(obj, dict) and (
                                "decisionIndex" in obj or "snapshotIndex" in obj
                            ):
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

Use the Decision number from the decision header as decisionIndex."""


def _eval_one_decision(
    client: OpenAI,
    model: str,
    system: str,
    user_msg: str,
    label: str,
    reasoning_effort: str | None = None,
) -> tuple[CallTrace, list[dict]]:
    """Evaluate a single decision and return (trace, annotations)."""
    trace = _call_llm(
        client,
        model,
        system,
        user_msg,
        reasoning_effort=reasoning_effort,
        label=label,
    )
    anns: list[dict] = []
    try:
        anns = _parse_json_array(trace.response_text)
    except (json.JSONDecodeError, AssertionError):
        print(f"    WARNING: Failed to parse response for {label}")
    return trace, anns


def _approach_per_decision(
    client: OpenAI,
    data: GameData,
    decisions: list[Decision],
    overview: str,
    model: str,
    approach_name: str,
    reasoning_effort: str | None = None,
) -> ExperimentResult:
    """Per-decision approach: one API call per non-forced decision."""
    result = ExperimentResult(approach=approach_name, game_id=data.id, model=model)
    non_forced = [d for d in decisions if not is_forced(d)]

    def make_task(d: Decision) -> tuple[str, str, str]:
        formatted = _format_decisions([d])
        user_msg = f"## Game Overview\n{overview}\n\n## Decision\n\n{formatted}"
        label = f"decision_{decision_index(d)}"
        return user_msg, label, PER_DECISION_SYSTEM

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}
        for d in non_forced:
            user_msg, label, system = make_task(d)
            fut = pool.submit(
                _eval_one_decision,
                client,
                model,
                system,
                user_msg,
                label,
                reasoning_effort=reasoning_effort,
            )
            futures[fut] = decision_index(d)

        # Collect results, preserving decision order for deterministic output
        results_by_idx: dict[int, tuple[CallTrace, list[dict]]] = {}
        for fut in as_completed(futures):
            idx = futures[fut]
            results_by_idx[idx] = fut.result()

    for d in non_forced:
        trace, anns = results_by_idx[decision_index(d)]
        result.calls.append(trace)
        result.annotations.extend(anns)

    return result


# --- Approach C: Extended thinking on Opus ---


def _approach_thinking(
    client: OpenAI,
    data: GameData,
    decisions: list[Decision],
    overview: str,
) -> ExperimentResult:
    """Approach C: Current single-pass with extended thinking enabled."""
    result = ExperimentResult(approach="C_thinking", game_id=data.id, model=OPUS)
    non_forced = [d for d in decisions if not is_forced(d)]

    user_msg = (
        "## Game Overview\n"
        f"{overview}\n\n"
        f"## Decisions ({len(non_forced)} non-forced)\n\n"
        f"{_format_decisions(decisions)}"
    )

    trace = _call_llm(
        client,
        OPUS,
        OPUS_SYSTEM,
        user_msg,
        reasoning_effort="high",
        label="full_game",
    )
    result.calls.append(trace)

    try:
        result.annotations = _parse_json_array(trace.response_text)
    except (json.JSONDecodeError, AssertionError) as e:
        print(f"    WARNING: Failed to parse thinking response: {e}")

    return result


# --- Baseline: Current v5 ---


def _approach_baseline(
    client: OpenAI,
    data: GameData,
    decisions: list[Decision],
    overview: str,
) -> ExperimentResult:
    """Baseline: Current single-pass Opus without thinking (v5 logic)."""
    result = ExperimentResult(approach="baseline", game_id=data.id, model=OPUS)
    non_forced = [d for d in decisions if not is_forced(d)]

    user_msg = (
        "## Game Overview\n"
        f"{overview}\n\n"
        f"## Decisions ({len(non_forced)} non-forced)\n\n"
        f"{_format_decisions(decisions)}"
    )

    trace = _call_llm(client, OPUS, OPUS_SYSTEM, user_msg, label="full_game")
    result.calls.append(trace)

    try:
        result.annotations = _parse_json_array(trace.response_text)
    except (json.JSONDecodeError, AssertionError) as e:
        print(f"    WARNING: Failed to parse baseline response: {e}")

    return result


# --- Approach F: Per-decision Opus, minimal context (no game overview) ---

MINIMAL_SYSTEM = f"""\
You are a Magic: The Gathering expert evaluating a single decision from a game replay.

Analyze the decision below. The board state is shown inline — use it to evaluate the play.
If the play was reasonable, return an empty JSON array: []
If it was a blunder, return a JSON array with one annotation object.

Most decisions are reasonable — only flag clear mistakes or questionable choices.

{SHARED_CATEGORIES}

{SHARED_SEVERITY}

## Output Format

Return ONLY a JSON array — either empty [] or containing one annotation object:
{ANNOTATION_SCHEMA}

Use the Decision number from the decision header as decisionIndex."""


def _approach_per_decision_minimal(
    client: OpenAI,
    data: GameData,
    decisions: list[Decision],
    _overview: str,
    model: str,
    approach_name: str,
) -> ExperimentResult:
    """Per-decision with minimal context: no game overview, just the decision + board state."""
    result = ExperimentResult(approach=approach_name, game_id=data.id, model=model)
    non_forced = [d for d in decisions if not is_forced(d)]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}
        for d in non_forced:
            formatted = _format_decisions([d])
            user_msg = f"## Decision\n\n{formatted}"
            label = f"decision_{decision_index(d)}"
            fut = pool.submit(
                _eval_one_decision, client, model, MINIMAL_SYSTEM, user_msg, label
            )
            futures[fut] = decision_index(d)

        results_by_idx: dict[int, tuple[CallTrace, list[dict]]] = {}
        for fut in as_completed(futures):
            idx = futures[fut]
            results_by_idx[idx] = fut.result()

    for d in non_forced:
        trace, anns = results_by_idx[decision_index(d)]
        result.calls.append(trace)
        result.annotations.extend(anns)

    return result


# --- Approach G: Flash pre-filter → Opus deep dive ---

FLASH_SCREEN_SYSTEM = f"""\
You are a Magic: The Gathering expert doing a quick review of a single decision.

Is this decision clearly correct, or does it deserve closer analysis?
Respond with ONLY one of:
- "PASS" if the play seems reasonable
- "FLAG" followed by a brief reason if the play might be a mistake

Be generous with flagging — flag anything that's even slightly questionable.
It's much worse to miss a real blunder than to flag a correct play.

{SHARED_CATEGORIES}"""

FLASH_SCREEN_SENSITIVE_SYSTEM = f"""\
You are a Magic: The Gathering expert screening decisions for potential mistakes.

Your job is to FILTER OUT only the decisions that are OBVIOUSLY correct — where there \
is essentially zero chance of a mistake. Everything else gets flagged for expert review.

Respond with ONLY one of:
- "PASS" — ONLY if the decision is trivially correct with no room for debate. Examples: \
only one reasonable option, forced play, routine mana tapping, obvious block.
- "FLAG" followed by a brief reason — for ANYTHING that could conceivably be questioned. \
When in doubt, always FLAG.

Your error budget is asymmetric: missing a real blunder is 100x worse than flagging a \
correct play. The expert review is cheap — your job is just to save time on the obvious ones.

{SHARED_CATEGORIES}"""


def _approach_flash_opus(
    client: OpenAI,
    data: GameData,
    decisions: list[Decision],
    overview: str,
) -> ExperimentResult:
    """Two-phase: Flash screens each decision, Opus analyzes flagged ones."""
    result = ExperimentResult(
        approach="G_flash_opus", game_id=data.id, model=f"{FLASH}+{OPUS}"
    )
    non_forced = [d for d in decisions if not is_forced(d)]

    # Phase 1: Flash screens each decision (parallel)
    def screen_one(d: Decision) -> tuple[int, CallTrace, bool]:
        formatted = _format_decisions([d])
        user_msg = f"## Game Overview\n{overview}\n\n## Decision\n\n{formatted}"
        trace = _call_llm(
            client,
            FLASH,
            FLASH_SCREEN_SYSTEM,
            user_msg,
            label=f"screen_{decision_index(d)}",
        )
        flagged = not trace.response_text.strip().upper().startswith("PASS")
        return decision_index(d), trace, flagged

    screen_results: dict[int, tuple[CallTrace, bool]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(screen_one, d): d for d in non_forced}
        for fut in as_completed(futs):
            idx, trace, was_flagged = fut.result()
            screen_results[idx] = (trace, was_flagged)

    flagged_decisions: list[Decision] = []
    for d in non_forced:
        trace, was_flagged = screen_results[decision_index(d)]
        result.calls.append(trace)
        if was_flagged:
            flagged_decisions.append(d)

    print(
        f"    Flash flagged {len(flagged_decisions)}/{len(non_forced)} decisions for Opus review"
    )

    # Phase 2: Opus analyzes flagged decisions (parallel)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        eval_futures: dict[Future[tuple[CallTrace, list[dict]]], int] = {}
        for d in flagged_decisions:
            formatted = _format_decisions([d])
            user_msg = f"## Game Overview\n{overview}\n\n## Decision\n\n{formatted}"
            label = f"opus_{decision_index(d)}"
            eval_fut = pool.submit(
                _eval_one_decision, client, OPUS, PER_DECISION_SYSTEM, user_msg, label
            )
            eval_futures[eval_fut] = decision_index(d)

        opus_results: dict[int, tuple[CallTrace, list[dict]]] = {}
        for eval_fut in as_completed(eval_futures):
            idx = eval_futures[eval_fut]
            opus_results[idx] = eval_fut.result()

    for d in flagged_decisions:
        trace, anns = opus_results[decision_index(d)]
        result.calls.append(trace)
        result.annotations.extend(anns)

    return result


# --- Approach Q: Flash screening + Sonnet low thinking ---


def _approach_flash_sonnet(
    client: OpenAI,
    data: GameData,
    decisions: list[Decision],
    overview: str,
) -> ExperimentResult:
    """Two-phase: sensitive Flash screens each decision, Sonnet+low analyzes flagged."""
    result = ExperimentResult(
        approach="Q_flash_sonnet", game_id=data.id, model=f"{FLASH}+{SONNET}"
    )
    non_forced = [d for d in decisions if not is_forced(d)]

    # Phase 1: Flash screens each decision (parallel) with sensitive prompt
    def screen_one(d: Decision) -> tuple[int, CallTrace, bool]:
        formatted = _format_decisions([d])
        user_msg = f"## Game Overview\n{overview}\n\n## Decision\n\n{formatted}"
        trace = _call_llm(
            client,
            FLASH,
            FLASH_SCREEN_SENSITIVE_SYSTEM,
            user_msg,
            label=f"screen_{decision_index(d)}",
        )
        flagged = not trace.response_text.strip().upper().startswith("PASS")
        return decision_index(d), trace, flagged

    screen_results: dict[int, tuple[CallTrace, bool]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(screen_one, d): d for d in non_forced}
        for fut in as_completed(futs):
            idx, trace, was_flagged = fut.result()
            screen_results[idx] = (trace, was_flagged)

    flagged_decisions: list[Decision] = []
    for d in non_forced:
        trace, was_flagged = screen_results[decision_index(d)]
        result.calls.append(trace)
        if was_flagged:
            flagged_decisions.append(d)

    print(
        f"    Flash flagged {len(flagged_decisions)}/{len(non_forced)} decisions for Sonnet review"
    )

    # Phase 2: Sonnet+low analyzes flagged decisions (parallel)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        eval_futures: dict[Future[tuple[CallTrace, list[dict]]], int] = {}
        for d in flagged_decisions:
            formatted = _format_decisions([d])
            user_msg = f"## Game Overview\n{overview}\n\n## Decision\n\n{formatted}"
            label = f"sonnet_{decision_index(d)}"
            eval_fut = pool.submit(
                _eval_one_decision,
                client,
                SONNET,
                PER_DECISION_SYSTEM,
                user_msg,
                label,
                reasoning_effort="low",
            )
            eval_futures[eval_fut] = decision_index(d)

        sonnet_results: dict[int, tuple[CallTrace, list[dict]]] = {}
        for eval_fut in as_completed(eval_futures):
            idx = eval_futures[eval_fut]
            sonnet_results[idx] = eval_fut.result()

    for d in flagged_decisions:
        trace, anns = sonnet_results[decision_index(d)]
        result.calls.append(trace)
        result.annotations.extend(anns)

    return result


# --- Approach H: Batched per-decision Opus (5 decisions per call) ---

BATCH_SIZE = 5

BATCHED_SYSTEM = f"""\
You are a Magic: The Gathering expert evaluating decisions from a game replay.

Analyze each decision below independently. For each one, decide if it was a blunder.
Return a single JSON array containing annotation objects for any blunders found.
If all decisions are reasonable, return an empty array: []

Each decision is self-contained with its own board state. Evaluate them independently.

{SHARED_CATEGORIES}

{SHARED_SEVERITY}

## Output Format

Return ONLY a JSON array of annotation objects (may be empty):
{ANNOTATION_SCHEMA}

Use the Decision number from each decision header as decisionIndex."""


def _approach_batched(
    client: OpenAI,
    data: GameData,
    decisions: list[Decision],
    overview: str,
    model: str,
    approach_name: str,
    reasoning_effort: str | None = None,
    batch_size: int = BATCH_SIZE,
) -> ExperimentResult:
    """Batched per-decision: send batch_size decisions per API call."""
    result = ExperimentResult(approach=approach_name, game_id=data.id, model=model)
    non_forced = [d for d in decisions if not is_forced(d)]

    # Build all batches
    batches: list[tuple[list[Decision], str, str]] = []
    for batch_start in range(0, len(non_forced), batch_size):
        batch = non_forced[batch_start : batch_start + batch_size]
        batch_indices = [decision_index(d) for d in batch]

        parts: list[str] = []
        for d in batch:
            formatted = _format_decisions([d])
            parts.append(f"--- DECISION ---\n{formatted}")

        user_msg = (
            f"## Game Overview\n{overview}\n\n## Decisions ({len(batch)} to evaluate)\n\n"
            + "\n\n".join(parts)
        )
        label = f"batch_{batch_indices[0]}-{batch_indices[-1]}"
        batches.append((batch, user_msg, label))

    # Run all batches in parallel
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}
        for i, (_batch, user_msg, label) in enumerate(batches):
            fut = pool.submit(
                _eval_one_decision,
                client,
                model,
                BATCHED_SYSTEM,
                user_msg,
                label,
                reasoning_effort=reasoning_effort,
            )
            futures[fut] = i

        results_by_idx: dict[int, tuple[CallTrace, list[dict]]] = {}
        for fut in as_completed(futures):
            idx = futures[fut]
            results_by_idx[idx] = fut.result()

    for i in range(len(batches)):
        trace, anns = results_by_idx[i]
        result.calls.append(trace)
        result.annotations.extend(anns)

    return result


# --- Approach I: Multi-turn conversation ---

CONVERSATION_SYSTEM = f"""\
You are a Magic: The Gathering expert annotating blunders in a game replay.

I'll send you decisions one at a time. For each decision, respond with EXACTLY one of:
1. PASS — if the play is reasonable.
2. A JSON annotation object — if the play is a blunder.

Keep responses short. Do NOT explain your reasoning for PASS decisions.

{SHARED_CATEGORIES}

{SHARED_SEVERITY}

## Annotation Format
{ANNOTATION_SCHEMA}

Use the Decision number from the decision header as decisionIndex."""


def _call_llm_messages(
    client: OpenAI,
    model: str,
    messages: list[dict],
    label: str = "",
) -> CallTrace:
    """Call LLM with a full messages list and return a CallTrace."""
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": 2048,
        "temperature": 0,
    }

    start = time.monotonic()
    response = client.chat.completions.create(**kwargs)
    elapsed = time.monotonic() - start

    text = response.choices[0].message.content
    assert text is not None, "LLM returned no content"
    usage = response.usage
    assert usage is not None, "API response missing usage data"

    cost = _compute_cost(model, usage.prompt_tokens, usage.completion_tokens)

    trace = CallTrace(
        model=model,
        system_prompt=messages[0]["content"] if messages else "",
        user_prompt=messages[-1]["content"] if messages else "",
        response_text=text,
        thinking_text="",
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        thinking_tokens=0,
        cost_usd=cost,
        wall_time_seconds=elapsed,
        label=label,
    )

    print(
        f"    [{label or 'call'}] {model} "
        f"{usage.prompt_tokens:,} in / {usage.completion_tokens:,} out "
        f"${cost:.4f} ({elapsed:.1f}s)"
    )

    return trace


def _approach_conversation(
    client: OpenAI,
    data: GameData,
    decisions: list[Decision],
    overview: str,
    model: str,
    approach_name: str,
) -> ExperimentResult:
    """Multi-turn conversation: send decisions one at a time, accumulate context."""
    result = ExperimentResult(approach=approach_name, game_id=data.id, model=model)
    non_forced = [d for d in decisions if not is_forced(d)]

    # Build conversation history incrementally
    messages: list[dict] = [
        {"role": "system", "content": CONVERSATION_SYSTEM},
        {
            "role": "user",
            "content": f"## Game Overview\n{overview}\n\nI'll now send you {len(non_forced)} decisions to evaluate.",
        },
        {"role": "assistant", "content": "Ready. Send the first decision."},
    ]

    for d in non_forced:
        formatted = _format_decisions([d])
        messages.append({"role": "user", "content": formatted})

        trace = _call_llm_messages(
            client,
            model,
            messages,
            label=f"decision_{decision_index(d)}",
        )
        result.calls.append(trace)

        # Add the assistant response to conversation history
        messages.append({"role": "assistant", "content": trace.response_text})

        # Parse response
        text = trace.response_text.strip()
        if not text.upper().startswith("PASS"):
            # Try to extract annotation
            parsed = _parse_inline_response(text)
            result.annotations.extend(parsed)

    return result


# --- Registry ---

APPROACHES: dict[str, tuple[str, object]] = {
    "baseline": ("Current v5 (single Opus, no thinking)", _approach_baseline),
    "A_inline": ("Inline annotation (single Opus, annotate-as-you-go)", None),
    "B_flash": ("Per-decision Gemini 2.5 Flash", None),
    "C_thinking": ("Extended thinking Opus (single pass)", _approach_thinking),
    "D_opus": ("Per-decision Opus", None),
    "E_sonnet": ("Per-decision Sonnet 4.5", None),
    "F_opus_minimal": ("Per-decision Opus, no game overview", None),
    "G_flash_opus": ("Flash pre-filter → Opus deep dive", None),
    "H_opus_batched": ("Batched Opus (5 decisions/call)", None),
    "I_convo_opus": ("Multi-turn conversation Opus", None),
    "J_convo_sonnet": ("Multi-turn conversation Sonnet", None),
    "K_opus_thinking": ("Per-decision Opus with extended thinking", None),
    "L_sonnet_thinking": ("Per-decision Sonnet with extended thinking", None),
    "M_sonnet_batched_medium": (
        "Batched Sonnet with medium thinking (5 decisions/call)",
        None,
    ),
    "N_sonnet_batched_high": (
        "Batched Sonnet with high thinking (5 decisions/call)",
        None,
    ),
    "O_sonnet_medium": (
        "Per-decision Sonnet with medium thinking",
        None,
    ),
    "P_sonnet_low": (
        "Per-decision Sonnet with low thinking",
        None,
    ),
    "Q_flash_sonnet": (
        "Flash screening + Sonnet low thinking",
        None,
    ),
}


def run_approach(
    approach: str,
    client: OpenAI,
    data: GameData,
    decisions: list[Decision],
    overview: str,
) -> ExperimentResult:
    """Run a specific approach and return the result."""
    if approach == "baseline":
        return _approach_baseline(client, data, decisions, overview)
    if approach == "A_inline":
        return _approach_inline(client, data, decisions, overview)
    if approach == "B_flash":
        return _approach_per_decision(
            client, data, decisions, overview, FLASH, "B_flash"
        )
    if approach == "C_thinking":
        return _approach_thinking(client, data, decisions, overview)
    if approach == "D_opus":
        return _approach_per_decision(client, data, decisions, overview, OPUS, "D_opus")
    if approach == "E_sonnet":
        return _approach_per_decision(
            client, data, decisions, overview, SONNET, "E_sonnet"
        )
    if approach == "F_opus_minimal":
        return _approach_per_decision_minimal(
            client, data, decisions, overview, OPUS, "F_opus_minimal"
        )
    if approach == "G_flash_opus":
        return _approach_flash_opus(client, data, decisions, overview)
    if approach == "H_opus_batched":
        return _approach_batched(
            client, data, decisions, overview, OPUS, "H_opus_batched"
        )
    if approach == "I_convo_opus":
        return _approach_conversation(
            client, data, decisions, overview, OPUS, "I_convo_opus"
        )
    if approach == "J_convo_sonnet":
        return _approach_conversation(
            client, data, decisions, overview, SONNET, "J_convo_sonnet"
        )
    if approach == "K_opus_thinking":
        return _approach_per_decision(
            client,
            data,
            decisions,
            overview,
            OPUS,
            "K_opus_thinking",
            reasoning_effort="high",
        )
    if approach == "L_sonnet_thinking":
        return _approach_per_decision(
            client,
            data,
            decisions,
            overview,
            SONNET,
            "L_sonnet_thinking",
            reasoning_effort="high",
        )
    if approach == "M_sonnet_batched_medium":
        return _approach_batched(
            client,
            data,
            decisions,
            overview,
            SONNET,
            "M_sonnet_batched_medium",
            reasoning_effort="medium",
        )
    if approach == "N_sonnet_batched_high":
        return _approach_batched(
            client,
            data,
            decisions,
            overview,
            SONNET,
            "N_sonnet_batched_high",
            reasoning_effort="high",
        )
    if approach == "O_sonnet_medium":
        return _approach_per_decision(
            client,
            data,
            decisions,
            overview,
            SONNET,
            "O_sonnet_medium",
            reasoning_effort="medium",
        )
    if approach == "P_sonnet_low":
        return _approach_per_decision(
            client,
            data,
            decisions,
            overview,
            SONNET,
            "P_sonnet_low",
            reasoning_effort="low",
        )
    if approach == "Q_flash_sonnet":
        return _approach_flash_sonnet(client, data, decisions, overview)
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
    results: list[dict] = []
    if not RESULTS_DIR.exists():
        return results
    for p in sorted(RESULTS_DIR.glob(f"{game_id}_*.json")):
        with open(p) as f:
            results.append(json.load(f))
    return results


def _print_comparison(game_id: str, results: list[dict], data: GameData) -> None:
    """Print a comparison table of approach results."""
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
            sev = a.get("severity")
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
        decisions = data.decisions
        num_decisions = len(decisions) if decisions is not None else 0
        for a in anns:
            dec = a.get("decisionIndex", "?")
            valid = "OK" if isinstance(dec, int) and 0 <= dec < num_decisions else "BAD"
            print(
                f"  [{valid}] decision={dec} {a.get('player', '?')} "
                f"{a.get('severity', '?').upper()} {a.get('category', '?')}"
            )
            a_desc = a.get("description")
            print(f"       {a_desc[:120] if a_desc else '(no description)'}")


def _dry_run(gz_path: str) -> None:
    """Print formatted inputs without calling APIs."""
    data = load_game_for_annotation(gz_path)
    decisions = extract_decisions(gz_path)
    non_forced = [d for d in decisions if not is_forced(d)]
    overview = game_overview(data)

    print(f"Game: {data.id}")
    print(f"Snapshots: {len(data.snapshots)}")
    print(f"Decisions: {len(decisions)} total, {len(non_forced)} non-forced")
    print()

    # Show what baseline/thinking would send
    all_formatted = _format_decisions(decisions)
    baseline_user = f"## Game Overview\n{overview}\n\n## Decisions ({len(non_forced)} non-forced)\n\n{all_formatted}"
    print("=== Baseline/Thinking input ===")
    print(f"System: {len(OPUS_SYSTEM)} chars (~{len(OPUS_SYSTEM) // 4} tokens)")
    print(f"User: {len(baseline_user)} chars (~{len(baseline_user) // 4} tokens)")
    print()

    # Show what per-decision would send for one decision
    if non_forced:
        d = non_forced[0]
        formatted = _format_decisions([d])
        per_dec_user = f"## Game Overview\n{overview}\n\n## Decision\n\n{formatted}"
        print(f"=== Per-decision input (decision {decision_index(d)}) ===")
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
        f"## Game Overview\n{overview}\n\n## Decisions ({len(non_forced)} total)\n\n"
        + "\n\n".join(parts)
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
    existing = data.annotations
    if existing:
        print(f"\n=== Existing v5 annotations ({len(existing)}) ===")
        for a in existing:
            print(f"  decision={a.decision_index} {a.player} {a.severity.upper()}")
            print(
                f"       {a.description[:120] if a.description else '(no description)'}"
            )


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

    data = load_game_for_annotation(args.game)
    game_id = data.id

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
    overview = game_overview(data)
    non_forced = [d for d in decisions if not is_forced(d)]

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
        result = run_approach(
            approach,
            client,
            data,
            decisions,
            overview,
        )
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
