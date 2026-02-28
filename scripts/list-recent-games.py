#!/usr/bin/env python3
"""List recent games from ~/mage-bench-logs/.

Usage:
    list-recent-games.py                   # 5 most recent games with metadata
    list-recent-games.py --count 10        # 10 most recent games
    list-recent-games.py --symlinks        # show all last-* symlinks
    list-recent-games.py --config round-robin-1v1 # resolve a config symlink
"""

import argparse
import json
import os
from pathlib import Path

LOGS_DIR = Path.home() / "mage-bench-logs"


def list_symlinks() -> None:
    for entry in sorted(LOGS_DIR.iterdir()):
        if entry.is_symlink() and entry.name.startswith("last-"):
            target = os.readlink(entry)
            print(f"{entry.name} -> {target}")


def resolve_config(config: str) -> None:
    link = LOGS_DIR / f"last-{config}"
    assert link.exists(), f"Symlink not found: {link}"
    target = os.readlink(link)
    print(target)


def list_games(count: int) -> None:
    game_dirs = sorted(
        (d for d in LOGS_DIR.iterdir() if d.is_dir() and d.name.startswith("game_")),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )[:count]

    for d in game_dirs:
        meta_path = d / "game_meta.json"
        if meta_path.exists():
            m = json.loads(meta_path.read_text())
            players = " vs ".join(p["name"] for p in m["players"])
            config = m.get("config", "?")
            deck_type = m.get("deck_type", "?")
            winner = m.get("winner", "?")
            print(f"{d.name}: {config} | {deck_type} | {players} | winner: {winner}")
        else:
            print(f"{d.name}: (no metadata)")


def main() -> None:
    parser = argparse.ArgumentParser(description="List recent games")
    parser.add_argument("--count", type=int, default=5, help="Number of games to list")
    parser.add_argument("--symlinks", action="store_true", help="Show last-* symlinks")
    parser.add_argument("--config", type=str, help="Resolve a config symlink")
    args = parser.parse_args()

    assert LOGS_DIR.is_dir(), f"Logs directory not found: {LOGS_DIR}"

    if args.symlinks:
        list_symlinks()
    elif args.config:
        resolve_config(args.config)
    else:
        list_games(args.count)


if __name__ == "__main__":
    main()
