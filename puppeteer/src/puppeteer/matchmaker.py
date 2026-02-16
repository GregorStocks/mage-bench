"""Yente: matchmaker for top-rated models.

Computes current ratings from game data and returns the subset of
gauntlet presets whose models are rated above a threshold. Used by
the config system when a player has preset="yente".
"""

from __future__ import annotations

import gzip
import json
import random
import sys
from pathlib import Path

from puppeteer.harness_epoch import MIN_LEADERBOARD_EPOCH
from puppeteer.leaderboard import (
    compute_elo_ratings,
    compute_openskill_ratings,
    derive_format,
)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_GAMES_DIR = _ROOT / "website" / "public" / "games"
_PRESETS_JSON = _ROOT / "puppeteer" / "presets.json"
_MODELS_JSON = _ROOT / "puppeteer" / "models.json"

_DEFAULT_THRESHOLD = 1600

_BLESSINGS = [
    "Matchmaker, matchmaker, make me a match!",
    "Find me a find, catch me a catch!",
    "For papa, make him a scholar...",
    "Matchmaker, matchmaker, look through your book!",
    "Night after night in the dark I'm alone, so find me a match of my own.",
    "Up to this minute, I misunderstood that I could get stuck for good!",
    "Playing with matches a girl can get burned!",
]


def _load_games_index(games_dir: Path) -> list[dict]:
    """Load minimal game index for rating computation."""
    games = []
    for gz_path in sorted(games_dir.glob("game_*.json.gz")):
        game = json.loads(gzip.decompress(gz_path.read_bytes()))
        games.append(
            {
                "id": game["id"],
                "timestamp": game.get("timestamp", ""),
                "gameType": game.get("gameType", ""),
                "deckType": game.get("deckType", ""),
                "winner": game.get("winner"),
                "players": game.get("players", []),
                "harnessEpoch": game.get("harnessEpoch"),
            }
        )
    return games


def _build_key_to_preset(presets_path: Path) -> dict[str, str]:
    """Build player_key -> preset_name mapping for gauntlet presets."""
    data = json.loads(presets_path.read_text())
    presets = data.get("presets", {})
    gauntlet = set(data.get("gauntlet", []))
    mapping: dict[str, str] = {}
    for name, pdata in presets.items():
        if name not in gauntlet:
            continue
        model_id = pdata.get("model", "")
        effort = pdata.get("reasoning_effort")
        key = f"{model_id}::{effort}" if effort else model_id
        mapping[key] = name
    return mapping


def _load_model_names(models_path: Path) -> dict[str, str]:
    """Load model_id -> display name mapping."""
    data = json.loads(models_path.read_text())
    return {m["id"]: m["name"] for m in data.get("models", [])}


def _display_key(key: str, model_names: dict[str, str]) -> str:
    """Format a player key for human-readable display."""
    model_id = key.split("::")[0]
    display = model_names.get(model_id, model_id)
    if "::" in key:
        display += f" ({key.split('::')[1]})"
    return display


def get_yente_pool(
    deck_type: str,
    threshold: int = _DEFAULT_THRESHOLD,
    games_dir: Path = _GAMES_DIR,
    presets_path: Path = _PRESETS_JSON,
    models_path: Path = _MODELS_JSON,
) -> list[str]:
    """Return gauntlet preset names for models rated above threshold.

    deck_type: the deckType from the config, used to determine whether
    to use Elo (1v1 formats) or OpenSkill (commander) ratings.

    Returns a list of preset name strings (e.g. ["sonnet-medium", "grok4f-medium"]).
    Prints a Fiddler quote and the eligible pool to stderr.
    """
    # Determine mode from deck type
    is_commander = "Commander" in deck_type or not deck_type

    # Load game data at current epoch
    all_games = _load_games_index(games_dir)
    rated_games = [g for g in all_games if g["harnessEpoch"] >= MIN_LEADERBOARD_EPOCH]

    # Compute ratings for the appropriate pool
    if is_commander:
        pool_games = [g for g in rated_games if derive_format(g) == "commander"]
        final_ratings, _per_game = compute_openskill_ratings(pool_games, games_dir)
        mode_label = "commander"
    else:
        pool_games = [g for g in rated_games if derive_format(g) != "commander"]
        final_ratings, _per_game = compute_elo_ratings(pool_games, games_dir)
        mode_label = "1v1"

    # Build reverse mapping: player_key -> preset name
    key_to_preset = _build_key_to_preset(presets_path)

    # Filter to models above threshold that have a gauntlet preset
    eligible: list[tuple[str, str, int]] = []  # (player_key, preset_name, rating)
    for key, rating in final_ratings.items():
        rating_int = int(rating)
        if rating_int >= threshold and key in key_to_preset:
            eligible.append((key, key_to_preset[key], rating_int))

    # Sort by rating descending for display
    eligible.sort(key=lambda x: -x[2])

    # Display the pool
    model_names = _load_model_names(models_path)
    print(f"  {random.choice(_BLESSINGS)}", file=sys.stderr)
    print(f"  Yente {mode_label} pool ({len(eligible)} models >= {threshold}):", file=sys.stderr)
    for key, preset, rating in eligible:
        display = _display_key(key, model_names)
        print(f"    {rating:4d}  {display}  [{preset}]", file=sys.stderr)

    return [preset for _, preset, _ in eligible]
