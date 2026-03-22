#!/usr/bin/env python3
"""CLI wrapper for `magebench.game.export_game`.

TODO(shim): expires=issue:python-migration-step12 Delete this entrypoint once
callers use `magebench.cli` wrappers directly.
"""

import sys
from pathlib import Path

from magebench.game import export_game as _export_game
from magebench.game.game_exports import GAMES_DIR as WEBSITE_GAMES_DIR
from magebench.leaderboard.website_data import generate_all_website_data

LOGS_DIR = _export_game.LOGS_DIR
GameExportError = _export_game.GameExportError
build_export = _export_game.build_export
export_game = _export_game.export_game
read_game_winner = _export_game.read_game_winner


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
