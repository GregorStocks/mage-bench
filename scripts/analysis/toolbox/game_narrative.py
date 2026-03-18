#!/usr/bin/env python3
"""Reconstruct game narrative from a game export (.json or .json.gz).

Prints turn-boundary board states and key actions (plays, casts, attacks, etc.).
"""

import sys

from scripts.analysis.blunder_eval_common import export_record_name, load_game

ACTION_KEYWORDS = [
    "plays",
    "casts",
    "attacks",
    "blocks",
    "damage",
    "destroys",
    "dies",
    "mulligans",
    "wins",
    "lost",
    "activates",
    "targets",
    "sacrifices",
]


def main(gz_path: str) -> None:
    d = load_game(gz_path)

    snapshots = d["snapshots"]
    actions = d["actions"]

    # Turn-boundary snapshots
    seen: set[int] = set()
    for s in snapshots:
        turn = s.get("turn", 0)
        if turn in seen:
            continue
        seen.add(turn)
        parts = []
        for p in s["players"]:
            bf = [export_record_name(c) for c in p["battlefield"]]
            hand = p.get("hand_count", len(p["hand"]))
            entry = f"{p['name']}: {p.get('life', '?')}hp hand={hand}"
            if bf:
                entry += f" bf=[{', '.join(bf)}]"
            parts.append(entry)
        print(f"Turn {turn}: {' | '.join(parts)}")

    print()

    # Key actions
    for a in actions:
        msg = a.message
        if msg is None:
            continue
        assert isinstance(msg, str), f"action message must be a string, got {msg!r}"
        is_key = any(kw in msg.lower() for kw in ACTION_KEYWORDS)
        is_chat = a.type == "chat"
        if is_key or is_chat:
            prefix = "[CHAT] " if is_chat else ""
            print(f"  {prefix}{msg[:200]}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <game.json.gz>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
