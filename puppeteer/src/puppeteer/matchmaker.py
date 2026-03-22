"""Matchmakers for model pairing.

Round-robin: fills coverage gaps in the matchup matrix (preset="round-robin").
"""

from __future__ import annotations

import gzip
import json
import random
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from magebench.common.json5_utils import loads_json5
from puppeteer.leaderboard import derive_format
from puppeteer.log import get_logger

logger = get_logger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_GAMES_DIR = _ROOT / "website" / "public" / "games"
_PRESETS_JSON = _ROOT / "puppeteer" / "presets.json"
_MODELS_JSON = _ROOT / "puppeteer" / "models.json"
_SEASON_JSON = _ROOT / "data" / "season.json"


def get_active_presets(presets_data: dict) -> list[str]:
    """Return names of presets with status='active'."""
    return [name for name, p in presets_data["presets"].items() if p.get("status") == "active"]


def _load_games_index(games_dir: Path) -> list[dict]:
    """Load minimal game index for rating computation."""
    fields = ("id", "timestamp", "gameType", "deckType", "winner", "players", "harnessEpoch", "season")
    defaults = {"players": [], "season": 0}
    games = []
    # Collect both .json5 and .json5.gz game files, deduplicating by stem
    seen_stems: set[str] = set()
    for path in sorted(games_dir.glob("game_*")):
        if path.suffix == ".gz" and path.name.endswith(".json5.gz"):
            stem = path.name.removesuffix(".json5.gz")
        elif path.suffix == ".json5":
            stem = path.stem
        else:
            continue
        if stem in seen_stems:
            continue
        seen_stems.add(stem)

        if path.name.endswith(".json5.gz"):
            game = loads_json5(gzip.decompress(path.read_bytes()))
        else:
            game = loads_json5(path.read_text())
        games.append({f: game.get(f, defaults.get(f)) for f in fields})
    return games


def _get_current_season(season_path: Path = _SEASON_JSON) -> int:
    """Read the current season number from data/season.json."""
    data = json.loads(season_path.read_text())
    season = data["current_season"]
    assert isinstance(season, int) and season >= 1, f"Invalid current_season: {season!r}"
    return season


def _load_rated_games(games_dir: Path, season_path: Path = _SEASON_JSON) -> list[dict]:
    """Load game index filtered to the current season only."""
    current = _get_current_season(season_path)
    return [g for g in _load_games_index(games_dir) if g["season"] == current]


def _build_key_to_preset(presets_path: Path) -> dict[str, str]:
    """Build player_key -> preset_name mapping for active presets."""
    data = json.loads(presets_path.read_text())
    presets = data["presets"]
    active = set(get_active_presets(data))
    mapping: dict[str, str] = {}
    for name, pdata in presets.items():
        if name not in active:
            continue
        model_id = pdata["model"]
        effort = pdata.get("reasoning_effort")
        key = f"{model_id}::{effort}" if effort else model_id
        mapping[key] = name
    return mapping


def _load_model_names(models_path: Path) -> dict[str, str]:
    """Load model_id -> display name mapping."""
    data = json.loads(models_path.read_text())
    assert isinstance(data, dict), f"{models_path}: expected JSON object"
    models = data["models"]
    assert isinstance(models, list), f"{models_path}: models must be a list"
    names: dict[str, str] = {}
    for index, model in enumerate(models):
        assert isinstance(model, dict), f"{models_path}: models[{index}] must be an object"
        model_id = model.get("id")
        model_name = model.get("name")
        assert isinstance(model_id, str) and model_id, f"{models_path}: models[{index}] missing id"
        assert isinstance(model_name, str) and model_name, f"{models_path}: models[{index}] missing name"
        names[model_id] = model_name
    return names


def _display_key(key: str, model_names: dict[str, str]) -> str:
    """Format a player key for human-readable display."""
    model_id = key.split("::")[0]
    display = model_names.get(model_id, model_id)
    if "::" in key:
        display += f" ({key.split('::')[1]})"
    return display


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
    model_id = player.get("model")
    assert isinstance(model_id, str), f"player model must be a string, got {model_id!r}"
    effort = player.get("reasoningEffort", player.get("reasoning_effort"))
    assert effort is None or isinstance(effort, str), (
        f"player reasoningEffort must be a string when present, got {effort!r}"
    )
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
        pilots = [p for p in game["players"] if p.get("type") == "pilot" and p.get("model")]
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
    deck_type: str | None,
    num_seats: int,
    games_dir: Path = _GAMES_DIR,
    presets_path: Path = _PRESETS_JSON,
    models_path: Path = _MODELS_JSON,
    extra_matchups: list[tuple[str, ...]] | None = None,
    season_path: Path = _SEASON_JSON,
) -> list[str]:
    """Return preset names for a coverage-maximizing matchup.

    Picks the group of num_seats active presets whose total pairwise
    matchup count is minimal. Ties broken by fewest total games played,
    then randomly.

    extra_matchups: preset tuples from earlier games in a parallel batch,
    counted as additional matchups in the matrix.

    Returns a list of exactly num_seats preset name strings.
    """
    is_commander = not deck_type or "Commander" in deck_type

    season_games = _load_rated_games(games_dir, season_path)

    # Filter by format
    if is_commander:
        pool_games = [g for g in season_games if derive_format(g) == "commander"]
        mode_label = "commander"
    else:
        pool_games = [g for g in season_games if derive_format(g) != "commander"]
        mode_label = "2-player"

    # Build mappings
    key_to_preset = _build_key_to_preset(presets_path)
    active = get_active_presets(json.loads(presets_path.read_text()))
    assert len(active) >= num_seats, f"Active pool has {len(active)} presets but need {num_seats} seats"

    # Build matchup matrix
    pair_counts, game_counts = _build_matchup_matrix(pool_games, key_to_preset, extra_matchups)

    # Score all possible groups
    candidates: list[tuple[int, int, tuple[str, ...]]] = []
    for combo in combinations(active, num_seats):
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
    logger.info("  %s", random.choice(_DISPATCHES))
    logger.info("  Round-robin %s matchup (%d seats):", mode_label, num_seats)
    for preset in selected:
        key = preset_to_key.get(preset, preset)
        display = _display_key(key, model_names)
        games = game_counts.get(preset, 0)
        logger.info("    %s  [%s]  (%d games)", display, preset, games)
    total_pairs = len(list(combinations(active, 2)))
    covered = sum(1 for pair in combinations(sorted(active), 2) if pair_counts.get(pair, 0) > 0)
    logger.info("  Coverage: %d/%d pairs have been played", covered, total_pairs)

    return selected


# --- Format rotation ---

_FORMAT_DISPATCHES = [
    "Shuffling the format deck...",
    "Rotating through the multiverse of formats...",
    "No format left behind.",
]


def pick_round_robin_format(
    candidates: list[str],
    selected_presets: list[str],
    games_dir: Path = _GAMES_DIR,
    presets_path: Path = _PRESETS_JSON,
    extra_format_picks: list[str] | None = None,
    season_path: Path = _SEASON_JSON,
) -> str:
    """Pick the format that best balances per-bot format distribution.

    candidates: list of deckType strings (e.g. ["Constructed - Standard", ...])
    selected_presets: the preset names for this game's players
    extra_format_picks: formats already picked by earlier games in a parallel batch

    Returns a single deckType string.
    """
    assert len(candidates) > 1, "pick_round_robin_format requires multiple candidates"

    season_games = _load_rated_games(games_dir, season_path)

    # Build key -> preset mapping
    key_to_preset = _build_key_to_preset(presets_path)

    # Count per-preset per-format games
    candidate_set = set(candidates)
    format_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for game in season_games:
        dt = game.get("deckType")
        if dt not in candidate_set:
            continue
        pilots = [p for p in game["players"] if p.get("type") == "pilot" and p.get("model")]
        for p in pilots:
            key = _player_key_from_dict(p)
            if key in key_to_preset:
                preset = key_to_preset[key]
                format_counts[preset][dt] += 1

    # Add virtual format picks from parallel batch
    if extra_format_picks:
        for fmt in extra_format_picks:
            for preset in selected_presets:
                format_counts[preset][fmt] += 1

    # Score each candidate: lower is better (fewest games for these players)
    scored: list[tuple[int, str]] = []
    for fmt in candidates:
        score = sum(format_counts[p][fmt] for p in selected_presets)
        scored.append((score, fmt))

    min_score = min(s[0] for s in scored)
    tied = [s[1] for s in scored if s[0] == min_score]
    chosen = random.choice(tied)

    # Display
    logger.info("  %s", random.choice(_FORMAT_DISPATCHES))
    for fmt in candidates:
        score = sum(format_counts[p][fmt] for p in selected_presets)
        marker = " <--" if fmt == chosen else ""
        logger.info("    %s: %d games for selected players%s", fmt, score, marker)

    return chosen
