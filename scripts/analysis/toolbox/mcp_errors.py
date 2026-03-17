#!/usr/bin/env python3
"""Analyze MCP tool errors from .json.gz game exports.

Reports error frequencies by error_code, error message, action type, model,
and whether errors were followed by a successful retry. Designed to identify
which error messages are least helpful to LLMs so we can improve them.

Usage:
    uv run python scripts/analysis/toolbox/mcp_errors.py <game.json.gz | directory>
"""

import json
import os
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from schemas.game_export_types import JsonObject, LlmEvent, is_pilot_player
from scripts.analysis.blunder_eval_common import load_game


@dataclass
class ErrorEvent:
    """A single MCP tool error occurrence."""

    player: str
    model: str
    tool: str
    error_code: str  # machine-readable code or "exception" for RuntimeException
    error_message: str
    action_type: str  # GAME_ASK, GAME_SELECT, etc. or "" if unknown
    args: JsonObject
    retryable: bool
    had_choices: bool  # whether error response included choices
    retry_outcome: str  # "success", "different_error", "same_error", "no_retry"
    game_id: str


def _parse_result(result_str: str) -> JsonObject | None:
    """Parse a tool result string into a dict, or None if not JSON."""
    try:
        r = json.loads(result_str)
        if isinstance(r, dict):
            return r
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _infer_error_code(error_message: str) -> str:
    """Infer error_code from error message text for older logs missing the field."""
    msg = error_message.lower()
    if "out of range" in msg:
        return "index_out_of_range"
    if "no pending action" in msg:
        return "no_pending_action"
    if "required" in msg or "provide " in msg or "mutually exclusive" in msg:
        return "missing_param"
    if "not a valid choice" in msg:
        return "invalid_choice"
    if "card not found" in msg:
        return "card_not_found"
    if "unknown action type" in msg:
        return "unknown_action_type"
    return "unknown"


def _infer_action_type(events: Sequence[LlmEvent], idx: int, player: str) -> str:
    """Look backward from a choose_action error to find the action_type from
    the preceding get_action_choices call."""
    for j in range(idx - 1, max(idx - 30, -1), -1):
        ev = events[j]
        if ev["player"] != player:
            continue
        if ev["type"] == "tool_call" and ev["tool"] == "get_action_choices":
            r = _parse_result(ev["result"])
            if r:
                action_type = r.get("action_type", "")
                assert isinstance(action_type, str), (
                    f"get_action_choices action_type must be a string, got {action_type!r}"
                )
                return action_type
        # Stop if we hit another choose_action from this player
        if ev["type"] == "tool_call" and ev["tool"] == "choose_action":
            break
    return ""


def _find_retry_outcome(
    events: Sequence[LlmEvent], idx: int, player: str, tool: str, error_code: str
) -> str:
    """Look forward from an error to see if the model retried and what happened."""
    for j in range(idx + 1, min(idx + 20, len(events))):
        ev = events[j]
        if ev["player"] != player:
            continue
        if ev["type"] != "tool_call":
            continue

        # If they called a different tool first (e.g. get_action_choices), keep looking
        if ev["tool"] != tool:
            continue

        r = _parse_result(ev["result"])
        if r is None:
            return "different_error"
        if r.get("success") is True:
            return "success"
        if r.get("error_code") == error_code:
            return "same_error"
        return "different_error"
    return "no_retry"


def analyze_game(gz_path: str) -> list[ErrorEvent]:
    """Extract all MCP errors from a single game export."""
    data = load_game(gz_path)

    game_id = data["id"]

    # Build player -> model mapping
    player_models: dict[str, str] = {}
    for p in data["players"]:
        if not is_pilot_player(p):
            continue
        player_models[p["name"]] = p["model"]

    events = data["llmEvents"]
    errors: list[ErrorEvent] = []

    for i, e in enumerate(events):
        if e["type"] != "tool_call":
            continue

        player = e["player"]
        assert player in player_models, (
            f"{game_id}: tool_call event for unknown pilot player {player!r}"
        )
        tool = e["tool"]
        model = player_models[player]
        result_str = e["result"]
        args = e["args"]

        r = _parse_result(result_str)

        if r is not None and r.get("success") is False:
            error_message = r.get("error", "")
            assert isinstance(error_message, str), (
                f"{game_id}: tool error message must be a string, got {error_message!r}"
            )
            error_code = r.get("error_code")
            assert error_code is None or isinstance(error_code, str), (
                f"{game_id}: tool error code must be a string when present, got {error_code!r}"
            )
            resolved_error_code = error_code or _infer_error_code(error_message)
            retryable = r.get("retryable", False)
            assert isinstance(retryable, bool), (
                f"{game_id}: tool retryable flag must be a bool, got {retryable!r}"
            )
            had_choices = "choices" in r

            action_type = ""
            if tool == "choose_action":
                action_type = _infer_action_type(events, i, player)

            retry_outcome = _find_retry_outcome(
                events, i, player, tool, resolved_error_code
            )

            errors.append(
                ErrorEvent(
                    player=player,
                    model=model,
                    tool=tool,
                    error_code=resolved_error_code,
                    error_message=error_message,
                    action_type=action_type,
                    args=args,
                    retryable=retryable,
                    had_choices=had_choices,
                    retry_outcome=retry_outcome,
                    game_id=game_id,
                )
            )

    return errors


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "0%"
    return f"{100 * n / total:.0f}%"


def report(all_errors: list[ErrorEvent], num_games: int) -> None:
    """Print analysis report."""
    if not all_errors:
        print(f"No MCP errors found across {num_games} games.")
        return

    # Count total tool calls for context (not available here, but we can count errors)
    print(
        f"=== MCP Error Analysis ({len(all_errors)} errors across {num_games} games) ===\n"
    )

    # --- Section 1: By error_code ---
    print("--- By error_code ---")
    code_counts = Counter(e.error_code for e in all_errors)
    for code, count in code_counts.most_common():
        retry_ok = sum(
            1
            for e in all_errors
            if e.error_code == code and e.retry_outcome == "success"
        )
        retry_same = sum(
            1
            for e in all_errors
            if e.error_code == code and e.retry_outcome == "same_error"
        )
        print(
            f"  {code}: {count}  (retry→success: {retry_ok}, retry→same_error: {retry_same})"
        )

    # --- Section 2: By error message (deduplicated) ---
    print("\n--- By error message ---")
    msg_counts = Counter(e.error_message for e in all_errors)
    for msg, count in msg_counts.most_common(20):
        retry_ok = sum(
            1
            for e in all_errors
            if e.error_message == msg and e.retry_outcome == "success"
        )
        retry_same = sum(
            1
            for e in all_errors
            if e.error_message == msg and e.retry_outcome == "same_error"
        )
        recovery_rate = _pct(retry_ok, count)
        print(f"  ({count}x, recovery {recovery_rate}) {msg}")
        if retry_same > 0:
            print(
                f"       ^ {retry_same} retried with same error (message not helping)"
            )

    # --- Section 3: By action_type (choose_action only) ---
    choose_errors = [
        e for e in all_errors if e.tool == "choose_action" and e.action_type
    ]
    if choose_errors:
        print("\n--- By action_type (choose_action errors) ---")
        type_counts = Counter(e.action_type for e in choose_errors)
        for atype, count in type_counts.most_common():
            retry_ok = sum(
                1
                for e in choose_errors
                if e.action_type == atype and e.retry_outcome == "success"
            )
            codes = Counter(
                e.error_code for e in choose_errors if e.action_type == atype
            )
            codes_str = ", ".join(f"{c}={n}" for c, n in codes.most_common(5))
            print(
                f"  {atype}: {count}  (recovery: {_pct(retry_ok, count)})  [{codes_str}]"
            )

    # --- Section 4: By model ---
    print("\n--- By model ---")
    models = sorted({e.model for e in all_errors})
    for model in models:
        model_errs = [e for e in all_errors if e.model == model]
        retry_ok = sum(1 for e in model_errs if e.retry_outcome == "success")
        retry_same = sum(1 for e in model_errs if e.retry_outcome == "same_error")
        codes = Counter(e.error_code for e in model_errs)
        codes_str = ", ".join(f"{c}={n}" for c, n in codes.most_common(5))
        print(
            f"  {model}: {len(model_errs)} errors "
            f"(recovery: {_pct(retry_ok, len(model_errs))}, "
            f"stuck: {_pct(retry_same, len(model_errs))})  [{codes_str}]"
        )

    # --- Section 5: Retry outcomes ---
    print("\n--- Retry outcomes ---")
    outcomes = Counter(e.retry_outcome for e in all_errors)
    for outcome, count in outcomes.most_common():
        print(f"  {outcome}: {count} ({_pct(count, len(all_errors))})")

    # --- Section 6: Worst error messages (highest same_error retry rate) ---
    print("\n--- Least helpful error messages (model retries with same error) ---")
    msg_stuck: list[tuple[str, int, int]] = []
    for msg, count in msg_counts.items():
        stuck = sum(
            1
            for e in all_errors
            if e.error_message == msg and e.retry_outcome == "same_error"
        )
        if stuck > 0:
            msg_stuck.append((msg, stuck, count))
    msg_stuck.sort(key=lambda x: x[1], reverse=True)
    for msg, stuck, total in msg_stuck[:15]:
        print(f"  ({stuck}/{total} stuck) {msg}")

    # --- Section 7: Non-choose_action errors ---
    other_errors = [e for e in all_errors if e.tool != "choose_action"]
    if other_errors:
        print("\n--- Non-choose_action tool errors ---")
        tool_counts = Counter((e.tool, e.error_message) for e in other_errors)
        for (tool, msg), count in tool_counts.most_common(10):
            print(f"  {tool}: ({count}x) {msg}")


def main(path: str) -> None:
    """Analyze a single game export or a directory of them."""
    if os.path.isdir(path):
        p = Path(path)
        gz_files = sorted(list(p.glob("*.json.gz")) + list(p.glob("*.json")))
        assert gz_files, f"No game files found in {path}"
        print(f"Analyzing {len(gz_files)} games from {path}\n")
        all_errors: list[ErrorEvent] = []
        for gz in gz_files:
            all_errors.extend(analyze_game(str(gz)))
        report(all_errors, len(gz_files))
    else:
        print(f"Analyzing {path}\n")
        all_errors = analyze_game(path)
        report(all_errors, 1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <game.json.gz | directory>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
