#!/usr/bin/env python3
"""Analyze a game for blunders using Opus 4.6 via OpenRouter.

Per-decision approach: sends each non-forced decision to Opus individually
for high-quality blunder detection.

Usage:
    uv run --project puppeteer python scripts/analysis/blunder_analysis.py <game.json.gz | game_id>

Accepts either a file path or a bare game ID (e.g. game_20260214_185313_g1).

Requires OPENROUTER_API_KEY environment variable.
"""

import json
import logging
import os
import sys
import tempfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from openai import OpenAI, OpenAIError

from magebench.game.game_export_types import (
    Action,
    Annotation,
    Decision,
    Snapshot,
    json_default,
)
from puppeteer.decision_renderer import chosen_display, render_decision
from puppeteer.llm_cost import fetch_openrouter_prices, get_model_price
from scripts.analysis.annotate_game import annotate_game
from scripts.analysis.blunder_context import (
    actions_by_turn,
    collect_card_names,
    format_current_turn_actions,
    format_prior_context,
    game_overview,
    get_oracle_texts,
)
from scripts.analysis.blunder_eval_common import (
    action_result,
    compute_aftermath_index,
    decision_index,
    game_path_for_id,
    is_cast_rolled_back,
    is_forced,
    is_mana_ability_subdecision,
    load_game_for_annotation,
    make_seed_entry,
    merge_into_ground_truth,
    reverse_map_annotations,
    snapshot_index,
)
from scripts.analysis.blunder_llm import (
    LLM_REQUIRED_FIELDS,
    call_llm,
    compute_cost,
    parse_annotation,
)
from scripts.analysis.blunder_prompts import PER_DECISION_SYSTEM, TOOL_REFERENCE
from scripts.analysis.extract_decisions import extract_decisions
from scripts.generate_leaderboard import generate_all_website_data

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
# v34: include preceding action context in prompt so the LLM knows what triggered
#      generic target-selection prompts (fixes Hunter's Insight misattribution)
BLUNDER_SCRIPT_VERSION = 34


class BlunderAnalysisError(RuntimeError):
    """Expected operational failure during blunder annotation."""


def _write_annotations(gz_path: str, annotations: list[Annotation]) -> None:
    """Write annotations (possibly empty) to the game file."""
    TMP_DIR.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, dir=str(TMP_DIR)
    ) as f:
        json.dump(annotations, f, default=json_default)
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


def _format_preceding_action(preceding: Decision) -> str:
    """Format a brief summary of the preceding decision for context.

    This helps the LLM understand what triggered a generic prompt like
    "Select a creature" — e.g. that a land was just played (triggering landfall),
    not that a spell like Hunter's Insight was cast.
    """
    msg = preceding.message
    di = decision_index(preceding)
    parts = [f"[Decision {di}] {msg}"]
    if preceding.chosen is not None:
        parts.append(
            f"→ Chose: {chosen_display(preceding.chosen, preceding.chosen_args, preceding.choices)}"
        )
    return "## Preceding Action\n\n" + " ".join(parts)


def build_decision_prompt(
    overview: str,
    decision: Decision,
    oracle_texts: dict[str, dict],
    snapshots: Sequence[Snapshot],
    actions_by_turn: dict[int, list[str]],
    num_players: int,
    all_actions: Sequence[Action],
    preceding_decision: Decision | None = None,
) -> tuple[str, str]:
    """Build the (system_prompt, user_message) pair for a single decision evaluation.

    Pure function with no side effects. Used by evaluate_one_decision() and
    tested via golden prompt tests.

    """
    snap_idx = snapshot_index(decision)
    snap = snapshots[snap_idx] if snap_idx < len(snapshots) else None

    preceding_ctx = ""
    if preceding_decision is not None:
        preceding_ctx = _format_preceding_action(preceding_decision)

    assert snap is not None, f"decision references missing snapshot index {snap_idx}"
    prior_ctx = format_prior_context(decision, snapshots, actions_by_turn, num_players)
    snap_ts = snap.ts
    turn_ctx = format_current_turn_actions(decision, all_actions, snap_ts)
    deciding_player = decision.player
    formatted = render_decision(
        decision,
        snap,
        oracle_texts=oracle_texts,
        deciding_player=deciding_player,
        include_card_reference=True,
        include_chosen=True,
        prior_context=prior_ctx,
        current_turn_actions=turn_ctx,
        preceding_action=preceding_ctx,
    )
    player = deciding_player
    user_msg = f"## Game Overview\n{overview}\n\nYou are evaluating **{player}**'s decision.\n\n{formatted}"

    if is_cast_rolled_back(decision):
        user_msg += (
            "\n\n**NOTE:** This cast was attempted but the game engine rolled it "
            "back because the player could not complete the mana payment. The spell "
            "never resolved — the net result was no action taken this priority window."
        )

    user_msg += f"\n\n{TOOL_REFERENCE}"

    return PER_DECISION_SYSTEM, user_msg


def evaluate_one_decision(
    client: OpenAI,
    model: str,
    prices: dict[str, tuple[float, float]],
    overview: str,
    decision: Decision,
    oracle_texts: dict[str, dict],
    snapshots: Sequence[Snapshot],
    actions_by_turn: dict[int, list[str]],
    num_players: int,
    all_actions: Sequence[Action],
    label: str | None = None,
    preceding_decision: Decision | None = None,
) -> tuple[list[Annotation], float, bool, dict]:
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
        preceding_decision=preceding_decision,
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
        text, in_tok, out_tok, cached_tok = call_llm(
            client, model, PER_DECISION_SYSTEM, user_msg
        )
        attempt_cost = compute_cost(prices, model, in_tok, out_tok)
        total_cost += attempt_cost
        suffix = f" (attempt {attempt + 1})" if attempt > 0 else ""
        cache_info = ""
        if cached_tok > 0 and in_tok > 0:
            cache_info = f" cache={cached_tok / in_tok * 100:.0f}%"
        print(
            f"  [{label}] {in_tok:,} in / {out_tok:,} out (${attempt_cost:.4f}){cache_info}{suffix}"
        )

        try:
            ann = parse_annotation(text)
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
        missing = LLM_REQUIRED_FIELDS - set(ann.keys())
        null_fields = {
            f for f in LLM_REQUIRED_FIELDS if f in ann and not isinstance(ann[f], str)
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
        "player": decision.player,
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

    # snapshotIndex points to the first snapshot AFTER the action resolved so
    # the viewer shows the annotation alongside its consequences.
    aftermath_idx = compute_aftermath_index(decision, snapshots)
    ann_obj = Annotation(
        type="blunder",
        decision_index=d_idx,
        snapshot_index=aftermath_idx,
        player=decision.player,
        severity=ann["severity"],
        description=ann["description"],
        action_taken=ann["actionTaken"],
        better_line=ann["betterLine"],
        llm_reasoning=ann.get("llmReasoning"),
    )

    return [ann_obj], cost, True, raw_record


def load_game_context(gz_path: str) -> dict:
    """Load and precompute all per-game context needed for eval.

    Shared by blunder_analysis.main() and blunder_eval.py.
    """
    data = load_game_for_annotation(gz_path)
    decisions = extract_decisions(gz_path)
    snapshots = data.snapshots
    overview = game_overview(data)
    game_actions = data.actions
    abt = actions_by_turn(game_actions)
    num_players = len(data.players)

    card_names = collect_card_names(data)
    oracle_texts = get_oracle_texts(sorted(card_names))

    # Preceding-decision lookup: for each decision, the one immediately before
    # it in the game sequence. Used by eval_decisions to give the annotator
    # context about what triggered generic prompts like "Select a creature".
    preceding_by_index: dict[int, Decision] = {}
    for i, d in enumerate(decisions):
        if i > 0:
            preceding_by_index[decision_index(d)] = decisions[i - 1]

    return {
        "data": data,
        "decisions": decisions,
        "preceding_by_index": preceding_by_index,
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
    decisions: Sequence[Decision],
    game_ctx: dict,
    client: OpenAI,
    prices: dict[str, tuple[float, float]],
) -> dict[int, tuple[list[Annotation], float, bool, dict]]:
    """Evaluate a list of decisions in parallel. Returns {decision_index: result}."""
    preceding_by_idx = game_ctx["preceding_by_index"]
    results_by_idx: dict[int, tuple[list[Annotation], float, bool, dict]] = {}

    pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    futures = {}
    for d in decisions:
        di = decision_index(d)
        fut = pool.submit(
            evaluate_one_decision,
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
            preceding_decision=preceding_by_idx.get(di),
        )
        futures[fut] = di

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
    annotations: Sequence[Annotation],
    decisions: Sequence[Decision],
) -> None:
    """Add annotated decisions to ground truth for future eval."""
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
    data = load_game_for_annotation(gz_path)
    if data.annotations is not None:
        existing_version = (
            data.blunder_script_version
            if data.blunder_script_version is not None
            else 1
        )
        if existing_version >= BLUNDER_SCRIPT_VERSION:
            print(
                f"Already analyzed (v{existing_version}): {gz_path} ({len(data.annotations)} annotations)"
            )
            return 0.0
        print(
            f"Reanalyzing: v{existing_version} → v{BLUNDER_SCRIPT_VERSION} ({gz_path})"
        )

    client, prices = init_api()

    overview = game_overview(data)
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
        chosen_args = d.get("chosenArgs")
        if d.get("chosen") is None and not ar and not chosen_args:
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
                prev_msg = decisions[j].get("message")
                assert isinstance(prev_msg, str) or prev_msg is None, (
                    f"decision message must be a string when present, got {prev_msg!r}"
                )
                if prev_msg and prev_msg.startswith(
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

    annotations: list[Annotation] = []
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
    num_snapshots = len(data.snapshots)
    valid_annotations: list[Annotation] = []
    for ann in annotations:
        idx = ann.snapshot_index
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
            game_id=data.id,
            decisions_analyzed=len(non_forced),
            total_prompt=total_prompt,
            total_completion=total_completion,
            total_cached=total_cached,
            total_cost=total_cost,
        )
        print(f"\nTotal cost: ${total_cost:.3f}")
        return total_cost

    # Display blunders
    snapshots = data.snapshots
    print(f"\nFound {len(annotations)} blunder(s):\n")
    for ann in annotations:
        snap_idx = ann.snapshot_index
        assert snap_idx is not None
        turn = snapshots[snap_idx].turn if snap_idx < len(snapshots) else "?"
        sev = ann.severity.upper()
        print(f"  Turn {turn} ({ann.player}) - {sev}")
        print(f"    {ann.description}")
        if ann.better_line:
            print(f"    Better: {ann.better_line}")
        print()

    _write_annotations(gz_path, annotations)

    # Auto-ingest: add annotated decisions to ground truth for future eval
    _auto_ingest_ground_truth(data.id, annotations, decisions)

    # Append run stats to blunder-stats.jsonl for internals tracking
    _append_blunder_stats(
        game_id=data.id,
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
    generate_all_website_data()
    print("Website data regenerated", file=sys.stderr)
