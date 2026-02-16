"""Matchmakers for model pairing.

Yente: filters to top-rated models (preset="yente").
Round-robin: fills coverage gaps in the matchup matrix (preset="round-robin").
"""

from __future__ import annotations

import gzip
import json
import random
import sys
from collections import defaultdict
from itertools import combinations
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


# --- Round-robin matchmaker ---

_DISPATCHES = [
    "Scouting uncharted matchup territory...",
    "Seeking the pairings no model has played...",
    "Mapping the unknown corners of the bracket...",
    "Every model deserves a chance to prove itself.",
    "Round-robin: no matchup left behind.",
    "Filling gaps in the great matchup matrix...",
]


def _player_key_from_dict(player: dict) -> str:
    """Build aggregation key from game player dict: 'model_id::effort' or 'model_id'."""
    model_id = player.get("model", "")
    effort = player.get("reasoningEffort") or player.get("reasoning_effort")
    if effort:
        return f"{model_id}::{effort}"
    return model_id


def _build_matchup_matrix(
    games: list[dict],
    key_to_preset: dict[str, str],
    extra_matchups: list[tuple[str, ...]] | None = None,
) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
    """Build pairwise matchup counts and per-preset game counts.

    Pair keys are (a, b) with a < b alphabetically.

    extra_matchups: preset tuples from earlier games in a parallel batch,
    counted as additional matchups in the matrix.

    Returns (pair_counts, game_counts).
    """
    pair_counts: dict[tuple[str, str], int] = defaultdict(int)
    game_counts: dict[str, int] = defaultdict(int)

    for game in games:
        pilots = [p for p in game.get("players", []) if p.get("type") == "pilot" and p.get("model")]
        preset_names = []
        for p in pilots:
            key = _player_key_from_dict(p)
            if key in key_to_preset:
                preset_names.append(key_to_preset[key])

        for name in preset_names:
            game_counts[name] += 1
        for a, b in combinations(sorted(preset_names), 2):
            pair_counts[(a, b)] += 1

    # Add virtual matchups from parallel batch
    if extra_matchups:
        for matchup in extra_matchups:
            for name in matchup:
                game_counts[name] += 1
            for a, b in combinations(sorted(matchup), 2):
                pair_counts[(a, b)] += 1

    return dict(pair_counts), dict(game_counts)


def get_round_robin_matchup(
    deck_type: str,
    num_seats: int,
    games_dir: Path = _GAMES_DIR,
    presets_path: Path = _PRESETS_JSON,
    models_path: Path = _MODELS_JSON,
    extra_matchups: list[tuple[str, ...]] | None = None,
) -> list[str]:
    """Return preset names for a coverage-maximizing matchup.

    Picks the group of num_seats gauntlet presets whose total pairwise
    matchup count is minimal. Ties broken by fewest total games played,
    then randomly.

    extra_matchups: preset tuples from earlier games in a parallel batch,
    counted as additional matchups in the matrix.

    Returns a list of exactly num_seats preset name strings.
    """
    is_commander = "Commander" in deck_type or not deck_type

    # Load game data at current epoch
    all_games = _load_games_index(games_dir)
    epoch_games = [g for g in all_games if (g.get("harnessEpoch") or 0) >= MIN_LEADERBOARD_EPOCH]

    # Filter by format
    if is_commander:
        pool_games = [g for g in epoch_games if derive_format(g) == "commander"]
        mode_label = "commander"
    else:
        pool_games = [g for g in epoch_games if derive_format(g) != "commander"]
        mode_label = "1v1"

    # Build mappings
    key_to_preset = _build_key_to_preset(presets_path)
    gauntlet = json.loads(presets_path.read_text()).get("gauntlet", [])
    assert len(gauntlet) >= num_seats, f"Gauntlet has {len(gauntlet)} presets but need {num_seats} seats"

    # Build matchup matrix
    pair_counts, game_counts = _build_matchup_matrix(pool_games, key_to_preset, extra_matchups)

    # Score all possible groups
    candidates: list[tuple[int, int, tuple[str, ...]]] = []
    for combo in combinations(gauntlet, num_seats):
        pair_score = sum(pair_counts.get(pair, 0) for pair in combinations(sorted(combo), 2))
        games_score = sum(game_counts.get(p, 0) for p in combo)
        candidates.append((pair_score, games_score, combo))

    # Find minimum score, collect all ties, pick randomly
    min_pair = min(c[0] for c in candidates)
    tied = [c for c in candidates if c[0] == min_pair]
    min_games = min(c[1] for c in tied)
    best = [c[2] for c in tied if c[1] == min_games]
    selected = list(random.choice(best))
    random.shuffle(selected)

    # Display
    model_names = _load_model_names(models_path)
    preset_to_key = {v: k for k, v in key_to_preset.items()}
    print(f"  {random.choice(_DISPATCHES)}", file=sys.stderr)
    print(f"  Round-robin {mode_label} matchup ({num_seats} seats):", file=sys.stderr)
    for preset in selected:
        key = preset_to_key.get(preset, preset)
        display = _display_key(key, model_names)
        games = game_counts.get(preset, 0)
        print(f"    {display}  [{preset}]  ({games} games)", file=sys.stderr)
    total_pairs = len(list(combinations(gauntlet, 2)))
    covered = sum(1 for pair in combinations(sorted(gauntlet), 2) if pair_counts.get(pair, 0) > 0)
    print(f"  Coverage: {covered}/{total_pairs} pairs have been played", file=sys.stderr)

    return selected
