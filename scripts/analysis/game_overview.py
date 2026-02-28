#!/usr/bin/env python3
"""Extract game overview from a game export (.json or .json.gz)."""

import sys

from blunder_eval_common import load_game


def main(gz_path: str) -> None:
    d = load_game(gz_path)

    print(f"Game: {d['id']}")
    print(f"Format: {d.get('deckType', '?')} ({d.get('gameType', '?')})")
    print(f"Turns: {d['totalTurns']}")
    print(f"Winner: {d['winner']}")
    for p in d["players"]:
        cost = p.get("totalCostUsd", 0)
        print(
            f"  {p['name']} ({p.get('model', '?')}) "
            f"- cost: ${cost:.2f} "
            f"- placement: {p.get('placement', '?')}"
        )

    errors = d.get("errors", [])
    if errors:
        print(f"\nCritical Errors: {len(errors)}")
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
