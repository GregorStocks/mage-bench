#!/usr/bin/env python3
"""Matchmaker, matchmaker, make me a match!

Computes current ratings from game data, filters to models above a
threshold, and writes a config JSON pairing top-rated models against
each other. Supports 1v1 (Elo) and commander (OpenSkill) modes.
"""

from __future__ import annotations

import argparse
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

_ROOT = Path(__file__).resolve().parent.parent
_GAMES_DIR = _ROOT / "website" / "public" / "games"
_PRESETS_JSON = _ROOT / "puppeteer" / "presets.json"
_MODELS_JSON = _ROOT / "puppeteer" / "models.json"
_DEFAULT_OUTPUT = _ROOT / "tmp" / "matchmaker.json"

_DEFAULT_THRESHOLD = 1600

_FORMAT_TO_DECK_TYPE = {
    "standard": "Constructed - Standard",
    "modern": "Constructed - Modern",
    "legacy": "Constructed - Legacy",
}

_BLESSINGS = [
    "Matchmaker, matchmaker, make me a match!",
    "Find me a find, catch me a catch!",
    "For papa, make him a scholar...",
    "Matchmaker, matchmaker, look through your book!",
    "Night after night in the dark I'm alone, so find me a match of my own.",
    "Up to this minute, I misunderstood that I could get stuck for good!",
    "Even a matchmaker learned how to switch...",
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


def matchmake(
    mode: str = "1v1",
    games_dir: Path = _GAMES_DIR,
    presets_path: Path = _PRESETS_JSON,
    models_path: Path = _MODELS_JSON,
    threshold: int = _DEFAULT_THRESHOLD,
    format_name: str | None = None,
) -> dict:
    """Generate a matchmaking config dict.

    mode: "1v1" picks 2 models using Elo ratings.
          "commander" picks 4 models using OpenSkill ratings.

    Returns the config dict (ready to write as JSON).
    Raises ValueError if not enough models qualify.
    """
    assert mode in ("1v1", "commander"), f"Unknown mode: {mode!r}"

    # Load game data at current epoch
    all_games = _load_games_index(games_dir)
    rated_games = [g for g in all_games if g["harnessEpoch"] >= MIN_LEADERBOARD_EPOCH]

    # Compute ratings for the appropriate pool
    if mode == "1v1":
        pool_games = [g for g in rated_games if derive_format(g) != "commander"]
        final_ratings, _per_game = compute_elo_ratings(pool_games, games_dir)
        num_players = 2
    else:
        pool_games = [g for g in rated_games if derive_format(g) == "commander"]
        final_ratings, _per_game = compute_openskill_ratings(pool_games, games_dir)
        num_players = 4

    # Build reverse mapping: player_key -> preset name
    key_to_preset = _build_key_to_preset(presets_path)

    # Filter to models above threshold that have a gauntlet preset
    eligible: list[tuple[str, str, int]] = []  # (player_key, preset_name, rating)
    for key, rating in final_ratings.items():
        rating_int = int(rating)
        if rating_int >= threshold and key in key_to_preset:
            eligible.append((key, key_to_preset[key], rating_int))

    if len(eligible) < num_players:
        raise ValueError(
            f"Only {len(eligible)} model(s) above threshold {threshold} in {mode} pool. "
            f"Need at least {num_players}. Try lowering --threshold."
        )

    # Pick players
    chosen = random.sample(eligible, num_players)

    # Display the matchup
    model_names = _load_model_names(models_path)
    print(f"  {random.choice(_BLESSINGS)}", file=sys.stderr)
    for key, preset, rating in chosen:
        display = _display_key(key, model_names)
        print(f"  {rating:4d}  {display}  [{preset}]", file=sys.stderr)

    # Build config
    if mode == "1v1":
        if format_name is None:
            format_name = random.choice(["standard", "modern", "legacy"])
        deck_type = _FORMAT_TO_DECK_TYPE[format_name]
        print(f"  Format: {format_name}", file=sys.stderr)
        config: dict = {
            "gameType": "Two Player Duel",
            "deckType": deck_type,
            "matchTimeLimit": "MIN__60",
            "matchBufferTime": "NONE",
            "players": [
                {
                    "type": "pilot",
                    "preset": c[1],
                    "personality": "random",
                    "deck": "random",
                }
                for c in chosen
            ],
        }
    else:
        print("  Format: commander", file=sys.stderr)
        config = {
            "matchTimeLimit": "MIN__60",
            "matchBufferTime": "NONE",
            "players": [
                {
                    "type": "pilot",
                    "preset": c[1],
                    "personality": "random",
                    "deck": "random",
                }
                for c in chosen
            ],
        }

    return config


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Matchmaker, matchmaker, make me a match!"
    )
    parser.add_argument(
        "mode",
        choices=["1v1", "commander"],
        help="Matchmaking mode: 1v1 (2 players, Elo) or commander (4 players, OpenSkill)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=_DEFAULT_THRESHOLD,
        help=f"Minimum rating to be eligible (default: {_DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--format",
        choices=["standard", "modern", "legacy"],
        default=None,
        help="Force a specific 1v1 format (default: random; ignored in commander mode)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help=f"Output config path (default: {_DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    config = matchmake(
        mode=args.mode, threshold=args.threshold, format_name=args.format
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(config, indent=2) + "\n")
    print(f"  Config written to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
