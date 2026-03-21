#!/usr/bin/env python3
"""Extract game overview from a game export (.json or .json.gz)."""

import sys

from scripts.analysis.blunder_eval_common import load_game


def main(gz_path: str) -> None:
    d = load_game(gz_path)

    print(f"Game: {d.id}")
    print(f"Format: {d.deck_type} ({d.game_type})")
    print(f"Turns: {d.total_turns}")
    print(f"Winner: {d.winner}")
    for p in d.players:
        cost = p.total_cost_usd or 0
        ok = p.tool_calls_ok
        fail = p.tool_calls_failed
        think = p.thinking_time_secs
        effort = p.reasoning_effort
        model: str = p.model or "?"
        if effort:
            model = f"{model} ({effort})"
        parts = [
            f"  {p.name} ({model})",
            f"cost: ${cost:.2f}",
            f"placement: {p.placement if p.placement is not None else '?'}",
            f"tools: {ok}ok/{fail}fail",
        ]
        if think is not None:
            parts.append(f"thinking: {think:.0f}s")
        print(" - ".join(parts))

    errors = d.errors
    if errors:
        print(f"\nCritical Errors: {len(errors)}")
        for err in errors:
            print(f"  [{err.ts}] [{err.source}] {err.player}: {err.message}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <game.json.gz>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
