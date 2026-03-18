#!/usr/bin/env python3
"""Sample LLM reasoning excerpts from a game export (.json or .json.gz).

Extracts 3-4 reasoning samples per player to assess decision quality.
"""

import sys

from schemas.game_export_types import LlmResponseEvent
from scripts.analysis.blunder_eval_common import load_game

MAX_SAMPLES = 4
MIN_REASONING_LEN = 50
EXCERPT_LEN = 600


def main(gz_path: str) -> None:
    d = load_game(gz_path)

    events = d["llmEvents"]
    players = sorted({e.player for e in events})

    for player in players:
        print(f"=== {player} ===")
        count = 0
        for e in events:
            if not isinstance(e, LlmResponseEvent) or e.player != player:
                continue
            reasoning = e.reasoning or e.thinking
            if reasoning and len(reasoning) > MIN_REASONING_LEN:
                count += 1
                print(f"--- Sample {count} ---")
                print(reasoning[:EXCERPT_LEN])
                print()
                if count >= MAX_SAMPLES:
                    break
        if count == 0:
            print("  (no reasoning samples)")
        print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <game.json.gz>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
