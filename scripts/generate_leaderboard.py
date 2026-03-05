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


def generate_all_website_data(
    games_dir: Path = WEBSITE_GAMES_DIR,
    data_dir: Path = WEBSITE_DATA_DIR,
    models_json: Path = MODELS_JSON,
) -> None:
    """Regenerate all website data: leaderboard, model stats, internals, blunder stats."""
    generate_leaderboard_file(games_dir, data_dir, models_json)
    generate_model_stats(games_dir, data_dir, models_json)
    generate_internals_data(games_dir, data_dir, models_json)
    generate_blunder_stats(data_dir)


def main() -> None:
    games_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else WEBSITE_GAMES_DIR
    data_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else WEBSITE_DATA_DIR
    models_json = Path(sys.argv[3]) if len(sys.argv) > 3 else MODELS_JSON

    generate_all_website_data(games_dir, data_dir, models_json)

    print("Website data regenerated")


if __name__ == "__main__":
    main()
