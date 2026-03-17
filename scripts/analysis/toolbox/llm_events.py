#!/usr/bin/env python3
"""Analyze LLM events and errors from a game export (.json or .json.gz).

Reports event type counts, failed tool calls, stalls/resets, and token usage.
"""

import json
import sys
from collections import Counter

from scripts.analysis.blunder_eval_common import load_game


def main(gz_path: str) -> None:
    d = load_game(gz_path)

    events = d["llmEvents"]
    if not events:
        print("No LLM events found.")
        return

    # Event type counts
    types = Counter(e["type"] for e in events)
    print("=== LLM Event Types ===")
    for t, c in types.most_common():
        print(f"  {t}: {c}")

    # By player
    print()
    players = sorted({e["player"] for e in events})
    for player in players:
        pe = [e for e in events if e["player"] == player]
        pt = Counter(e["type"] for e in pe)
        print(f"{player}: {dict(pt.most_common())}")

    # Failed tool calls
    print()
    print("=== Failed Tool Calls ===")
    fail_count = 0
    for tc in events:
        if tc["type"] != "tool_call":
            continue
        result = tc["result"]
        is_failure = False
        try:
            result_obj = json.loads(result)
            if isinstance(result_obj, dict) and result_obj.get("success") is False:
                is_failure = True
        except (json.JSONDecodeError, TypeError):
            # Fallback for very old logs without JSON structure
            if any(
                x in result.lower()
                for x in ["error", "out of range", "required", "invalid", "failed"]
            ):
                is_failure = True
        if is_failure:
            fail_count += 1
            print(
                f"  {tc['player']} | {tc['tool']} "
                f"| args={json.dumps(tc['args'])} "
                f"| {result[:200]}"
            )
    if fail_count == 0:
        print("  (none)")

    # Stalls, resets, auto-pilot, errors
    print()
    for t in ("stall", "context_reset", "auto_pilot_mode", "llm_error"):
        evts = [e for e in events if e["type"] == t]
        if evts:
            print(f"{t}: {len(evts)} events")

    # Token/cost summary
    responses = [e for e in events if e["type"] == "llm_response" and e.get("usage")]
    print()
    print("=== Token Usage ===")
    for player in players:
        pr = [e for e in responses if e["player"] == player]
        if not pr:
            continue
        prompt_tokens = sum(e["usage"].get("promptTokens", 0) for e in pr)
        completion_tokens = sum(e["usage"].get("completionTokens", 0) for e in pr)
        print(
            f"{player}: {len(pr)} responses, {prompt_tokens:,} prompt, {completion_tokens:,} completion tokens"
        )

    # Game-level errors from error logs
    errors = d.get("errors")
    if errors:
        print()
        print(f"=== Game Errors ({len(errors)}) ===")
        for err in errors:
            print(
                f"  [{err.get('ts', '?')}] [{err.get('source', '?')}] "
                f"{err.get('player', '?')}: {err.get('message', '?')}"
            )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <game.json.gz>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
