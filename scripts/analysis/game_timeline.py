#!/usr/bin/env python3
"""Show a chronological timeline of events from a .json.gz game export.

General-purpose game event viewer with filtering by turn range, player,
and mana-related events.

Usage:
    uv run python scripts/analysis/game_timeline.py <game.json.gz>
    uv run python scripts/analysis/game_timeline.py <game_id> --mana
    uv run python scripts/analysis/game_timeline.py <game.json.gz> --turns 3-5 --player "kimi25"
"""

import argparse
import gzip
import json
import os
import sys
from pathlib import Path


GAMES_DIR = (
    Path(__file__).resolve().parent.parent.parent / "website" / "public" / "games"
)

MANA_KEYWORDS = {
    "mana_plan",
    "auto_tap",
    "GAME_PLAY_MANA",
    "GAME_PLAY_XMANA",
    "GAME_CHOOSE_ABILITY",
}
MANA_CHAT_KEYWORDS = {"Spell cancelled", "mana plan", "not enough mana"}


def resolve_game_path(path_or_id: str) -> str:
    """Resolve a game ID or path to a .json.gz file path."""
    if os.path.isfile(path_or_id):
        return path_or_id
    # Try as game ID in the games directory
    candidate = GAMES_DIR / f"{path_or_id}.json.gz"
    if candidate.is_file():
        return str(candidate)
    # Try glob match
    matches = sorted(GAMES_DIR.glob(f"*{path_or_id}*.json.gz"))
    assert matches, f"No game found matching '{path_or_id}' in {GAMES_DIR}"
    if len(matches) > 1:
        print("Multiple matches, using first:", file=sys.stderr)
        for m in matches[:5]:
            print(f"  {m.name}", file=sys.stderr)
    return str(matches[0])


def load_game(gz_path: str) -> dict:
    with gzip.open(gz_path, "rt") as f:
        return json.load(f)


def parse_turn_range(s: str) -> tuple[int, int]:
    """Parse '3-5' or '3' into (start, end) inclusive."""
    if "-" in s:
        parts = s.split("-", 1)
        return int(parts[0]), int(parts[1])
    n = int(s)
    return n, n


def find_turn_at_ts(snapshots: list[dict], ts: str) -> int | None:
    """Find the turn number at a given timestamp."""
    best_turn = None
    for snap in snapshots:
        if snap.get("ts", "") <= ts:
            best_turn = snap.get("turn")
        else:
            break
    return best_turn


def find_context_at_ts(snapshots: list[dict], ts: str) -> str:
    """Find turn/phase context at a given timestamp."""
    best = None
    for snap in snapshots:
        if snap.get("ts", "") <= ts:
            best = snap
        else:
            break
    if best is None:
        return ""
    turn = best.get("turn", "?")
    phase = best.get("phase", "")
    active = best.get("active_player", "")
    parts = [f"T{turn}"]
    if phase:
        parts.append(phase)
    if active:
        parts.append(f"({active})")
    return " ".join(parts)


def _has_real_mana_plan(args: dict) -> bool:
    """Check if args contain a non-empty mana_plan (not just default [])."""
    mp = args.get("mana_plan")
    return mp is not None and isinstance(mp, list) and len(mp) > 0


def _has_real_auto_tap(args: dict) -> bool:
    """Check if auto_tap is meaningfully set (True, or False with a mana_plan)."""
    at = args.get("auto_tap")
    if at is None:
        return False
    if at is True:
        return True
    # auto_tap=False is only meaningful when paired with a real mana_plan
    return _has_real_mana_plan(args)


def is_mana_event(event: dict) -> bool:
    """Check if an event is mana-related."""
    etype = event.get("type", "")

    if etype == "tool_call":
        tool = event.get("tool", "")
        args = event.get("args", {})
        result_str = event.get("result", "")

        # choose_action with real mana_plan or meaningful auto_tap
        if tool == "choose_action":
            if _has_real_mana_plan(args):
                return True
            if _has_real_auto_tap(args):
                return True

        # Any tool result with mana-related action_type or chat
        if tool in ("get_action_choices", "choose_action", "pass_priority"):
            try:
                result = json.loads(result_str)
            except (json.JSONDecodeError, TypeError):
                return False
            action_type = result.get("action_type", "")
            if action_type in MANA_KEYWORDS:
                return True
            for msg in result.get("recent_chat", []):
                if any(kw in str(msg) for kw in MANA_CHAT_KEYWORDS):
                    return True
            if result.get("mana_plan_set"):
                return True

    return False


def fmt_args(tool: str, args: dict) -> str:
    """Format tool call args readably, skipping default/empty values."""
    if tool == "choose_action":
        parts = []
        # Core selection params — skip 0/None/empty defaults
        idx = args.get("index")
        if idx is not None and idx != 0:
            parts.append(f"index={idx}")
        elif idx == 0 and not args.get("id"):
            parts.append("index=0")
        aid = args.get("id")
        if aid and aid.strip():
            parts.append(f"id={aid}")
        answer = args.get("answer")
        if answer is not None and answer is not False:
            parts.append(f"answer={answer}")
        elif answer is False and idx is None and not aid:
            # answer=false alone means pass/cancel
            parts.append("answer=false")
        # Mana params — only show when meaningful
        if _has_real_mana_plan(args):
            parts.append(f"mana_plan={args['mana_plan']}")
        if _has_real_auto_tap(args):
            parts.append(f"auto_tap={args['auto_tap']}")
        elif args.get("auto_tap") is True:
            parts.append("auto_tap=true")
        # Other non-default params
        amount = args.get("amount")
        if amount is not None and amount != 0:
            parts.append(f"amount={amount}")
        attackers = args.get("attackers")
        if attackers and len(attackers) > 0:
            parts.append(f"attackers={attackers}")
        blockers = args.get("blockers")
        if blockers and len(blockers) > 0:
            parts.append(f"blockers={blockers}")
        text = args.get("text")
        if text and text.strip():
            parts.append(f"text={text!r}")
        return ", ".join(parts) if parts else "(no significant args)"
    elif tool == "get_action_choices":
        until = args.get("until")
        return f"until={until}" if until else ""
    elif tool == "pass_priority":
        until = args.get("until")
        return f"until={until}" if until else ""
    else:
        return json.dumps(args)[:200]


def fmt_result(tool: str, result_str: str, verbose: bool = False) -> str:
    """Format tool result readably."""
    try:
        result = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return result_str[:200] if result_str else "(no result)"

    if not isinstance(result, dict):
        return str(result)[:200]

    parts = []

    if tool == "choose_action":
        if result.get("success") is False:
            parts.append(f"FAILED: {result.get('error', '?')}")
            if result.get("error_code"):
                parts.append(f"[{result['error_code']}]")
        else:
            parts.append(result.get("action_taken", "ok"))
        if result.get("mana_plan_set"):
            parts.append(f"mana_plan_set({result.get('mana_plan_size', '?')})")
        if result.get("next_action_pending"):
            parts.append(f"-> {result.get('next_action_type', '?')}")
        if result.get("warning"):
            parts.append(f"WARNING: {result['warning']}")

    elif tool == "get_action_choices":
        if not result.get("action_pending"):
            parts.append("(no action pending)")
        else:
            at = result.get("action_type", "?")
            msg = result.get("message", "")
            choices = result.get("choices", [])
            parts.append(f"{at}: {msg[:80]}")
            if choices and verbose:
                for c in choices[:8]:
                    name = c.get("name", c.get("description", "?"))
                    idx = c.get("index", "?")
                    cid = c.get("id", "")
                    action = c.get("action", "")
                    mc = c.get("mana_cost", "")
                    extra = f" ({action})" if action else ""
                    extra += f" {mc}" if mc else ""
                    parts.append(f"  [{idx}]{' ' + cid if cid else ''} {name}{extra}")
                if len(choices) > 8:
                    parts.append(f"  ... +{len(choices) - 8} more")
            elif choices:
                parts.append(f"  {len(choices)} choices")
            # Mana pool
            pool = result.get("mana_pool")
            if pool and any(v for v in pool.values()):
                pool_str = " ".join(f"{k}={v}" for k, v in pool.items() if v)
                parts.append(f"  pool: {pool_str}")

    elif tool == "pass_priority":
        reason = result.get("stop_reason", "?")
        passed = result.get("actions_passed", 0)
        parts.append(f"{reason} (passed {passed})")
        if result.get("action_pending"):
            at = result.get("action_type", "?")
            parts.append(f"-> {at}")

    # Check for recent_chat with mana messages
    for msg in result.get("recent_chat", []):
        msg_str = str(msg)
        if any(kw in msg_str for kw in MANA_CHAT_KEYWORDS):
            parts.append(f"  CHAT: {msg_str[:120]}")

    return " | ".join(parts) if parts else str(result)[:200]


def print_event(
    event: dict,
    snapshots: list[dict],
    mana_only: bool,
    verbose: bool,
) -> bool:
    """Print a single event. Returns True if printed."""
    etype = event.get("type", "")
    ts = event.get("ts", "")
    player = event.get("player", "")
    # Short timestamp (just time portion)
    ts_short = ts.split("T")[-1][:12] if "T" in ts else ts[:12]
    context = find_context_at_ts(snapshots, ts)

    is_mana = is_mana_event(event)
    if mana_only and not is_mana:
        return False

    prefix = "[MANA] " if is_mana else ""

    if etype == "tool_call":
        tool = event.get("tool", "")
        args = event.get("args", {})
        result_str = event.get("result", "")
        latency = event.get("latencyMs", 0)

        args_fmt = fmt_args(tool, args)
        result_fmt = fmt_result(tool, result_str, verbose=verbose)

        print(f"{ts_short} {context:<30} {player:<25} {prefix}{tool}({args_fmt})")
        print(f"{'':>12} {'':>30} {'':>25}   -> {result_fmt}")
        if latency > 5000:
            print(f"{'':>12} {'':>30} {'':>25}   ({latency}ms)")
        return True

    elif etype == "llm_response":
        reasoning = event.get("reasoning", "")
        tool_calls = event.get("toolCalls", [])
        usage = event.get("usage", {})
        cost = event.get("costUsd", 0)

        if mana_only and not reasoning:
            return False

        tc_summary = ", ".join(tc.get("name", "?") for tc in tool_calls)
        prompt_t = usage.get("promptTokens", 0)
        comp_t = usage.get("completionTokens", 0)

        print(
            f"{ts_short} {context:<30} {player:<25} {prefix}LLM -> {tc_summary} (${cost:.4f}, {prompt_t}+{comp_t} tok)"
        )
        if reasoning and verbose:
            # Show first 200 chars of reasoning
            r = reasoning.replace("\n", " ")[:200]
            print(f"{'':>12} {'':>30} {'':>25}   reasoning: {r}")
        return True

    elif etype == "game_start":
        print(f"{ts_short} {'':>30} {player:<25} === GAME START ===")
        return True

    elif etype in ("stall", "context_reset", "llm_error"):
        detail = event.get("reason", event.get("error", ""))
        print(
            f"{ts_short} {context:<30} {player:<25} *** {etype.upper()}: {str(detail)[:100]} ***"
        )
        return True

    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Game event timeline viewer")
    parser.add_argument("game", help="Game .json.gz path or game ID")
    parser.add_argument("--turns", help="Turn range, e.g. '3-5' or '3'")
    parser.add_argument(
        "--player", help="Filter to a specific player name (substring match)"
    )
    parser.add_argument(
        "--mana", action="store_true", help="Show only mana-related events"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show choices, reasoning, and extra detail",
    )
    args = parser.parse_args()

    gz_path = resolve_game_path(args.game)
    data = load_game(gz_path)

    # Header
    print(f"Game: {data.get('id', '?')}")
    print(f"Format: {data.get('deckType', '?')} ({data.get('gameType', '?')})")
    print(f"Turns: {data.get('totalTurns', '?')} | Winner: {data.get('winner', '?')}")
    for p in data.get("players", []):
        model = p.get("model", "?")
        deck = p.get("deckName", "?")
        cost = p.get("totalCostUsd", 0)
        print(f"  {p['name']} ({model}) — {deck} — ${cost:.2f}")
    print()

    snapshots = data.get("snapshots", [])
    events = data.get("llmEvents", [])

    # Parse turn range filter
    turn_range = None
    if args.turns:
        turn_range = parse_turn_range(args.turns)

    # Filter and print events
    printed = 0
    for event in events:
        # Player filter
        if args.player:
            ep = event.get("player", "")
            if args.player.lower() not in ep.lower():
                continue

        # Turn filter
        if turn_range is not None:
            ts = event.get("ts", "")
            turn = find_turn_at_ts(snapshots, ts)
            if turn is not None and (turn < turn_range[0] or turn > turn_range[1]):
                continue

        if print_event(event, snapshots, mana_only=args.mana, verbose=args.verbose):
            printed += 1

    print(f"\n({printed} events shown)")


if __name__ == "__main__":
    main()
