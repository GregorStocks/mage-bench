#!/usr/bin/env python3
"""Show a chronological timeline of events from a .json.gz game export.

General-purpose game event viewer with filtering by turn range, player,
and mana-related events.

Usage:
    uv run python scripts/analysis/toolbox/game_timeline.py <game.json.gz>
    uv run python scripts/analysis/toolbox/game_timeline.py <game_id> --mana
    uv run python scripts/analysis/toolbox/game_timeline.py <game.json.gz> --turns 3-5 --player "kimi25"
"""

import argparse
import json
import os
import sys
from collections.abc import Sequence

from scripts.analysis.blunder_eval_common import GAMES_DIR
from scripts.analysis.blunder_eval_common import load_game as _load_game_common
from schemas.game_export_types import GameExport, JsonObject, LlmEvent, Snapshot

MANA_KEYWORDS = {
    "mana_plan",
    "auto_tap",
    "GAME_PLAY_MANA",
    "GAME_PLAY_XMANA",
    "GAME_CHOOSE_ABILITY",
}
MANA_CHAT_KEYWORDS = {"Spell cancelled", "mana plan", "not enough mana"}


def resolve_game_path(path_or_id: str) -> str:
    """Resolve a game ID or path to a game file path."""
    if os.path.isfile(path_or_id):
        return path_or_id
    # Try as game ID in the games directory
    for ext in (".json.gz", ".json"):
        candidate = GAMES_DIR / f"{path_or_id}{ext}"
        if candidate.is_file():
            return str(candidate)
    # Try glob match
    matches = sorted(
        list(GAMES_DIR.glob(f"*{path_or_id}*.json.gz"))
        + list(GAMES_DIR.glob(f"*{path_or_id}*.json"))
    )
    assert matches, f"No game found matching '{path_or_id}' in {GAMES_DIR}"
    if len(matches) > 1:
        print("Multiple matches, using first:", file=sys.stderr)
        for m in matches[:5]:
            print(f"  {m.name}", file=sys.stderr)
    return str(matches[0])


def load_game(gz_path: str) -> GameExport:
    return _load_game_common(gz_path)


def parse_turn_range(s: str) -> tuple[int, int]:
    """Parse '3-5' or '3' into (start, end) inclusive."""
    if "-" in s:
        parts = s.split("-", 1)
        return int(parts[0]), int(parts[1])
    n = int(s)
    return n, n


def _find_snapshot_index_by_seq(snapshots: Sequence[Snapshot], seq: int) -> int | None:
    """Find the nearest snapshot at or before a game sequence number."""
    best: int | None = None
    for i, snap in enumerate(snapshots):
        snap_seq = snap["seq"]
        if snap_seq <= seq:
            best = i
        else:
            break
    return best


def _find_snapshot_index_by_ts(snapshots: Sequence[Snapshot], ts: str) -> int | None:
    """Find the nearest snapshot at or before a timestamp."""
    best: int | None = None
    for i, snap in enumerate(snapshots):
        snap_ts = snap.get("ts")
        if snap_ts is None:
            continue
        if snap_ts <= ts:
            best = i
        else:
            break
    return best


def _find_snapshot_index_for_event(
    snapshots: Sequence[Snapshot], event: LlmEvent
) -> int | None:
    """Resolve the snapshot index for an event using the best available coordinate."""
    game_seq = event.get("gameSeq")
    if isinstance(game_seq, int) and not isinstance(game_seq, bool):
        return _find_snapshot_index_by_seq(snapshots, game_seq)
    ts = event.get("ts")
    if isinstance(ts, str) and ts:
        return _find_snapshot_index_by_ts(snapshots, ts)
    return None


def find_turn_for_event(snapshots: Sequence[Snapshot], event: LlmEvent) -> int | None:
    """Find the turn number for an event."""
    snap_idx = _find_snapshot_index_for_event(snapshots, event)
    if snap_idx is None:
        return None
    return snapshots[snap_idx]["turn"]


def find_context_for_event(snapshots: Sequence[Snapshot], event: LlmEvent) -> str:
    """Find turn/phase context for an event."""
    snap_idx = _find_snapshot_index_for_event(snapshots, event)
    if snap_idx is None:
        return ""
    best = snapshots[snap_idx]
    turn = best["turn"]
    phase_value = best["phase"]
    phase = phase_value if phase_value is not None else ""
    active_value = best["active_player"]
    active = active_value if active_value is not None else ""
    parts = [f"T{turn}"]
    if phase:
        parts.append(phase)
    if active:
        parts.append(f"({active})")
    return " ".join(parts)


def _has_real_mana_plan(args: JsonObject) -> bool:
    """Check if args contain a non-empty mana_plan (not just default [])."""
    mp = args.get("mana_plan")
    return mp is not None and isinstance(mp, list) and len(mp) > 0


def _has_real_auto_tap(args: JsonObject) -> bool:
    """Check if auto_tap is meaningfully set (True, or False with a mana_plan)."""
    at = args.get("auto_tap")
    if at is None:
        return False
    if at is True:
        return True
    # auto_tap=False is only meaningful when paired with a real mana_plan
    return _has_real_mana_plan(args)


def is_mana_event(event: LlmEvent) -> bool:
    """Check if an event is mana-related."""
    etype = event["type"]

    if etype == "tool_call":
        tool = event.get("tool", "")
        assert isinstance(tool, str), f"tool must be a string, got {tool!r}"
        args_raw = event.get("args")
        if args_raw is None:
            args = {}
        else:
            assert isinstance(args_raw, dict), (
                f"args must be an object, got {args_raw!r}"
            )
            args = args_raw
        assert isinstance(args, dict), f"args must be an object, got {args!r}"
        result_str = event.get("result", "")
        assert isinstance(result_str, str), (
            f"result must be a string when present, got {result_str!r}"
        )

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
            if not isinstance(result, dict):
                return False
            action_type = result.get("action_type", "")
            if isinstance(action_type, str) and action_type in MANA_KEYWORDS:
                return True
            recent_chat = result.get("recent_chat", [])
            if not isinstance(recent_chat, list):
                recent_chat = []
            for msg in recent_chat:
                if any(kw in str(msg) for kw in MANA_CHAT_KEYWORDS):
                    return True
            if result.get("mana_plan_set"):
                return True

    return False


def fmt_args(tool: str, args: JsonObject) -> str:
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
        if isinstance(aid, str) and aid.strip():
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
        if isinstance(attackers, list) and attackers:
            parts.append(f"attackers={attackers}")
        blockers = args.get("blockers")
        if isinstance(blockers, list) and blockers:
            parts.append(f"blockers={blockers}")
        text = args.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(f"text={text!r}")
        return ", ".join(parts) if parts else "(no significant args)"
    elif tool == "get_action_choices":
        until = args.get("until")
        return f"until={until}" if isinstance(until, str) and until else ""
    elif tool == "pass_priority":
        until = args.get("until")
        return f"until={until}" if isinstance(until, str) and until else ""
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
            action_taken = result.get("action_taken", "ok")
            parts.append(str(action_taken))
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
            assert isinstance(msg, str), f"message must be a string, got {msg!r}"
            parts.append(f"{at}: {msg[:80]}")
            if isinstance(choices, list) and choices and verbose:
                for c in choices[:8]:
                    if not isinstance(c, dict):
                        continue
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
            elif isinstance(choices, list) and choices:
                parts.append(f"  {len(choices)} choices")
            # Mana pool
            pool = result.get("mana_pool")
            if isinstance(pool, dict) and any(v for v in pool.values()):
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
    recent_chat = result.get("recent_chat", [])
    if not isinstance(recent_chat, list):
        recent_chat = []
    for msg in recent_chat:
        msg_str = str(msg)
        if any(kw in msg_str for kw in MANA_CHAT_KEYWORDS):
            parts.append(f"  CHAT: {msg_str[:120]}")

    return " | ".join(parts) if parts else str(result)[:200]


def print_event(
    event: LlmEvent,
    snapshots: list[Snapshot],
    mana_only: bool,
    verbose: bool,
) -> bool:
    """Print a single event. Returns True if printed."""
    etype = event["type"]
    ts = event.get("ts", "")
    assert isinstance(ts, str), f"event ts must be a string when present, got {ts!r}"
    player = event["player"]
    # Short timestamp (just time portion)
    ts_short = ts.split("T")[-1][:12] if "T" in ts else ts[:12]
    context = find_context_for_event(snapshots, event)

    is_mana = is_mana_event(event)
    if mana_only and not is_mana:
        return False

    prefix = "[MANA] " if is_mana else ""

    if etype == "tool_call":
        tool = event.get("tool", "")
        assert isinstance(tool, str), f"tool must be a string, got {tool!r}"
        args_raw = event.get("args")
        if args_raw is None:
            args = {}
        else:
            assert isinstance(args_raw, dict), (
                f"args must be an object, got {args_raw!r}"
            )
            args = args_raw
        assert isinstance(args, dict), f"args must be an object, got {args!r}"
        result_str = event.get("result", "")
        assert isinstance(result_str, str), (
            f"result must be a string when present, got {result_str!r}"
        )
        latency = event.get("latencyMs", 0)
        assert isinstance(latency, int), (
            f"latencyMs must be an int when present, got {latency!r}"
        )

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
        assert isinstance(reasoning, str) or reasoning is None, (
            f"reasoning must be a string when present, got {reasoning!r}"
        )
        if not isinstance(tool_calls, list):
            tool_calls = []
        if not isinstance(usage, dict):
            usage = {}
        if not isinstance(cost, (int, float)) or isinstance(cost, bool):
            cost = 0.0

        if mana_only and not reasoning:
            return False

        tc_summary = ", ".join(
            str(tc.get("name", "?")) for tc in tool_calls if isinstance(tc, dict)
        )
        prompt_t = usage.get("promptTokens", 0)
        comp_t = usage.get("completionTokens", 0)
        if not isinstance(prompt_t, int):
            prompt_t = 0
        if not isinstance(comp_t, int):
            comp_t = 0

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
        detail = event.get("reason", event.get("errorMessage", ""))
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
    print(f"Game: {data['id']}")
    print(f"Format: {data['deckType']} ({data['gameType']})")
    print(f"Turns: {data['totalTurns']} | Winner: {data['winner']}")
    for p in data["players"]:
        model = p.get("model", "?")
        deck = p.get("deckName", "?")
        cost = p.get("totalCostUsd", 0)
        print(f"  {p['name']} ({model}) — {deck} — ${cost:.2f}")
    print()

    snapshots = data["snapshots"]
    events = data["llmEvents"]

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
            turn = find_turn_for_event(snapshots, event)
            if turn is None:
                continue
            if turn < turn_range[0] or turn > turn_range[1]:
                continue

        if print_event(event, snapshots, mana_only=args.mana, verbose=args.verbose):
            printed += 1

    print(f"\n({printed} events shown)")


if __name__ == "__main__":
    main()
