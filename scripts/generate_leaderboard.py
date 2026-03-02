#!/usr/bin/env python3
"""Generate leaderboard JSON from game data for the website."""

import sys
from pathlib import Path

from puppeteer.leaderboard import (
    generate_blunder_stats,
    generate_internals_data,
    generate_leaderboard_file,
    generate_model_stats,
)

_ROOT = Path(__file__).resolve().parent.parent
WEBSITE_GAMES_DIR = _ROOT / "website" / "public" / "games"
WEBSITE_DATA_DIR = _ROOT / "website" / "src" / "data"
MODELS_JSON = _ROOT / "puppeteer" / "models.json"


def main() -> None:
    games_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else WEBSITE_GAMES_DIR
    data_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else WEBSITE_DATA_DIR
    models_json = Path(sys.argv[3]) if len(sys.argv) > 3 else MODELS_JSON

    output_path = generate_leaderboard_file(games_dir, data_dir, models_json)
    print(f"Leaderboard generated -> {output_path}")

    stats_path = generate_model_stats(games_dir, data_dir, models_json)
    print(f"Model stats generated -> {stats_path}")

    internals_path = generate_internals_data(games_dir, data_dir, models_json)
    print(f"Internals data generated -> {internals_path}")

    blunder_path = generate_blunder_stats(data_dir)
    print(f"Blunder stats generated -> {blunder_path}")


if __name__ == "__main__":
    main()
