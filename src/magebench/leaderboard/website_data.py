"""Website-data orchestration for leaderboard-related outputs."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from magebench.leaderboard.leaderboard import generate_leaderboard_file
from magebench.leaderboard.stats import (
    generate_blunder_stats,
    generate_internals_data,
    generate_model_stats,
)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
WEBSITE_GAMES_DIR = _ROOT / "website" / "public" / "games"
WEBSITE_DATA_DIR = _ROOT / "website" / "src" / "data"
MODELS_JSON = _ROOT / "puppeteer" / "models.json"
SEASON_JSON = _ROOT / "data" / "season.json"


def copy_season_data(
    data_dir: Path = WEBSITE_DATA_DIR,
    season_json: Path = SEASON_JSON,
) -> Path:
    """Copy the authoritative season state into the website data directory."""
    data_dir.mkdir(parents=True, exist_ok=True)
    dst = data_dir / "season.json"
    shutil.copy2(season_json, dst)
    return dst


def generate_all_website_data(
    games_dir: Path = WEBSITE_GAMES_DIR,
    data_dir: Path = WEBSITE_DATA_DIR,
    models_json: Path = MODELS_JSON,
    season_json: Path = SEASON_JSON,
) -> None:
    """Regenerate all website data: leaderboard, model stats, internals, blunder stats."""
    copy_season_data(data_dir=data_dir, season_json=season_json)
    season_data = json.loads(season_json.read_text())
    generate_leaderboard_file(
        games_dir,
        data_dir,
        models_json,
        current_season=season_data["current_season"],
    )
    generate_model_stats(games_dir, data_dir, models_json)
    generate_internals_data(games_dir, data_dir, models_json)
    generate_blunder_stats(data_dir)
