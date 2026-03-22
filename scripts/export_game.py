#!/usr/bin/env python3
"""CLI wrapper for `magebench.game.export_game`."""

import sys
from pathlib import Path

from magebench.game.export_game import (
    LOGS_DIR,
    GameExportError,
    build_export,
    export_game,
    read_game_winner,
)
from magebench.game.game_exports import GAMES_DIR as WEBSITE_GAMES_DIR
from scripts.generate_leaderboard import generate_all_website_data

__all__ = [
    "LOGS_DIR",
    "WEBSITE_GAMES_DIR",
    "GameExportError",
    "build_export",
    "export_game",
    "read_game_winner",
]


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <game_id> [website_games_dir]")
        print(f"  game_id: directory name under {LOGS_DIR}")
        sys.exit(1)

    game_id = sys.argv[1]
    game_dir = LOGS_DIR / game_id
    if not game_dir.is_dir():
        print(f"Error: {game_dir} is not a directory")
        sys.exit(1)

    games_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else WEBSITE_GAMES_DIR
    output_path = export_game(game_dir, games_dir)
    size_kb = output_path.stat().st_size // 1024
    print(f"Exported {game_id} -> {output_path} ({size_kb} KB)")

    generate_all_website_data(games_dir=games_dir)
    print("Website data regenerated")


if __name__ == "__main__":
    main()
