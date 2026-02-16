#!/usr/bin/env python3
"""Generate a matchmaking config pairing top-rated 1v1 models.

Computes current Elo ratings from game data, filters to models above a
threshold, and writes a 1v1 config JSON pairing two of them.
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import sys
from pathlib import Path

from puppeteer.harness_epoch import MIN_LEADERBOARD_EPOCH
from puppeteer.leaderboard import compute_elo_ratings, derive_format

_ROOT = Path(__file__).resolve().parent.parent
_GAMES_DIR = _ROOT / "website" / "public" / "games"
_PRESETS_JSON = _ROOT / "puppeteer" / "presets.json"
_MODELS_JSON = _ROOT / "puppeteer" / "models.json"
_DEFAULT_OUTPUT = _ROOT / "tmp" / "matchmake.json"

_DEFAULT_THRESHOLD = 1600

_FORMAT_TO_DECK_TYPE = {
    "standard": "Constructed - Standard",
    "modern": "Constructed - Modern",
    "legacy": "Constructed - Legacy",
}


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


def matchmake(
    games_dir: Path = _GAMES_DIR,
    presets_path: Path = _PRESETS_JSON,
    models_path: Path = _MODELS_JSON,
    threshold: int = _DEFAULT_THRESHOLD,
    format_name: str | None = None,
) -> dict:
    """Generate a matchmaking config dict.

    Returns the config dict (ready to write as JSON).
    Raises ValueError if fewer than 2 models qualify.
    """
    # Load game data and filter to 1v1 games at current epoch
    all_games = _load_games_index(games_dir)
    rated_games = [g for g in all_games if g["harnessEpoch"] >= MIN_LEADERBOARD_EPOCH]
    games_1v1 = [g for g in rated_games if derive_format(g) != "commander"]

    # Compute Elo ratings
    final_ratings, _per_game = compute_elo_ratings(games_1v1, games_dir)

    # Build reverse mapping: player_key -> preset name
    key_to_preset = _build_key_to_preset(presets_path)

    # Filter to models above threshold that have a gauntlet preset
    eligible: list[tuple[str, str, int]] = []  # (player_key, preset_name, rating)
    for key, rating in final_ratings.items():
        rating_int = int(rating)
        if rating_int >= threshold and key in key_to_preset:
            eligible.append((key, key_to_preset[key], rating_int))

    if len(eligible) < 2:
        raise ValueError(
            f"Only {len(eligible)} model(s) above threshold {threshold}. "
            f"Need at least 2. Try lowering --threshold."
        )

    # Pick 2 random models
    chosen = random.sample(eligible, 2)

    # Pick format
    if format_name is None:
        format_name = random.choice(["standard", "modern", "legacy"])
    deck_type = _FORMAT_TO_DECK_TYPE[format_name]

    # Load display names for logging
    model_names = _load_model_names(models_path)

    for key, preset, rating in chosen:
        model_id = key.split("::")[0]
        display = model_names.get(model_id, model_id)
        effort_part = f" ({key.split('::')[1]})" if "::" in key else ""
        print(f"  {rating:4d}  {display}{effort_part}  [{preset}]", file=sys.stderr)

    print(f"  Format: {format_name}", file=sys.stderr)

    # Build config
    config = {
        "gameType": "Two Player Duel",
        "deckType": deck_type,
        "matchTimeLimit": "MIN__60",
        "matchBufferTime": "NONE",
        "players": [
            {
                "type": "pilot",
                "preset": chosen[0][1],
                "personality": "random",
                "deck": "random",
            },
            {
                "type": "pilot",
                "preset": chosen[1][1],
                "personality": "random",
                "deck": "random",
            },
        ],
    }
    return config


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate matchmaking config for top-rated models"
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=_DEFAULT_THRESHOLD,
        help=f"Minimum Elo rating to be eligible (default: {_DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--format",
        choices=["standard", "modern", "legacy"],
        default=None,
        help="Force a specific format (default: random)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help=f"Output config path (default: {_DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    config = matchmake(threshold=args.threshold, format_name=args.format)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(config, indent=2) + "\n")
    print(f"Config written to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
