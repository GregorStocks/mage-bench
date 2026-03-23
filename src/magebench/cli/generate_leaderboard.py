"""Regenerate website leaderboard and aggregate website data."""

import sys
from pathlib import Path

from magebench.leaderboard.website_data import (
    MODELS_JSON,
    WEBSITE_DATA_DIR,
    WEBSITE_GAMES_DIR,
    generate_all_website_data,
)


def main() -> None:
    games_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else WEBSITE_GAMES_DIR
    data_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else WEBSITE_DATA_DIR
    models_json = Path(sys.argv[3]) if len(sys.argv) > 3 else MODELS_JSON

    generate_all_website_data(games_dir, data_dir, models_json)
    print("Website data regenerated")


if __name__ == "__main__":
    main()
