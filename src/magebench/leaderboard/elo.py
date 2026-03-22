"""Elo and placement helpers for leaderboard generation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from magebench.game.game_export_types import Player
from magebench.leaderboard.common import load_game_file

_LOST_GAME_RE = re.compile(r"^(.+?) has lost the game\.$")

_ELO_START = 1600
_ELO_K = 32


def player_key(model: str, reasoning_effort: str | None = None) -> str:
    """Build aggregation key: 'model_id::effort' or just 'model_id'."""
    if reasoning_effort:
        return f"{model}::{reasoning_effort}"
    return model


def split_key(key: str) -> tuple[str, str | None]:
    """Split aggregation key into (model_id, reasoning_effort)."""
    if "::" in key:
        model_id, effort = key.split("::", 1)
        return model_id, effort
    return key, None


def extract_placements(
    game: Mapping[str, object], games_dir: Path | None = None
) -> dict[str, int]:
    """Extract player placements from game data."""
    players_obj = game["players"]
    assert isinstance(players_obj, list), (
        f"game {game.get('id', '<unknown>')}: players must be a list"
    )
    players: list[Player] = []
    for index, player in enumerate(players_obj):
        assert isinstance(player, Player), (
            f"game {game.get('id', '<unknown>')}: players[{index}] must be a Player"
        )
        players.append(player)

    if any(player.placement is not None for player in players):
        existing_placements: dict[str, int] = {}
        for player in players:
            if player.placement is None:
                continue
            existing_placements[player.name] = player.placement
        return existing_placements

    if games_dir is None:
        return _placements_from_winner(game)

    game_path = games_dir / f"{game['id']}.json5.gz"
    if not game_path.exists():
        game_path = games_dir / f"{game['id']}.json5"
    if not game_path.exists():
        return _placements_from_winner(game)

    full_game = load_game_file(game_path)
    actions = full_game.actions
    player_names = [player.name for player in players]
    winner = game.get("winner")
    assert winner is None or isinstance(winner, str), (
        f"game {game.get('id', '<unknown>')}: winner must be a string or null"
    )

    eliminations = []
    for action in actions:
        message = action.message
        match = _LOST_GAME_RE.match(message) if message else None
        if match:
            eliminations.append(match.group(1))

    placements: dict[str, int] = {}
    if winner:
        placements[winner] = 1
        for index, name in enumerate(reversed(eliminations)):
            placements[name] = index + 2
    elif eliminations:
        surviving = [name for name in player_names if name not in eliminations]
        for name in surviving:
            placements[name] = 1
        for index, name in enumerate(reversed(eliminations)):
            placements[name] = len(surviving) + index + 1

    return placements


def _placements_from_winner(game: Mapping[str, object]) -> dict[str, int]:
    """Minimal placement extraction using only winner field."""
    winner = game.get("winner")
    assert winner is None or isinstance(winner, str), (
        f"game {game.get('id', '<unknown>')}: winner must be a string or null"
    )
    if not winner:
        return {}
    placements: dict[str, int] = {}
    players_obj = game["players"]
    assert isinstance(players_obj, list), (
        f"game {game.get('id', '<unknown>')}: players must be a list"
    )
    for index, player in enumerate(players_obj):
        assert isinstance(player, Player), (
            f"game {game.get('id', '<unknown>')}: players[{index}] must be a Player"
        )
        placements[player.name] = 1 if player.name == winner else 2
    return placements


def compute_elo_ratings(
    games_index: list[dict[str, Any]],
    games_dir: Path | None = None,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Compute Elo ratings from 1v1 game history."""
    ratings: dict[str, float] = {}
    per_game: list[dict[str, Any]] = []

    sorted_games = sorted(
        games_index, key=lambda game: game["timestamp"] if "timestamp" in game else ""
    )

    for game in sorted_games:
        pilots = [
            player
            for player in game["players"]
            if isinstance(player, Player) and player.type == "pilot" and player.model
        ]
        if len(pilots) < 2:
            for pilot in pilots:
                assert isinstance(pilot.model, str) and pilot.model
                key = player_key(pilot.model, pilot.reasoning_effort)
                if key not in ratings:
                    ratings[key] = float(_ELO_START)
            if pilots:
                assert isinstance(pilots[0].model, str) and pilots[0].model
                key = player_key(pilots[0].model, pilots[0].reasoning_effort)
                per_game.append(
                    {
                        "id": game["id"],
                        "players": [
                            {
                                "key": key,
                                "ratingBefore": round(ratings[key]),
                                "ratingAfter": round(ratings[key]),
                            }
                        ],
                    }
                )
            continue

        for pilot in pilots:
            assert isinstance(pilot.model, str) and pilot.model
            key = player_key(pilot.model, pilot.reasoning_effort)
            if key not in ratings:
                ratings[key] = float(_ELO_START)

        pilot_keys: list[str] = []
        for pilot in pilots:
            assert isinstance(pilot.model, str) and pilot.model
            pilot_keys.append(player_key(pilot.model, pilot.reasoning_effort))

        before = {key: round(ratings[key]) for key in pilot_keys}

        placements = extract_placements(game, games_dir)
        has_placements = any(pilot.name in placements for pilot in pilots)

        if has_placements and len(pilots) == 2:
            key_a, key_b = pilot_keys
            rating_a, rating_b = ratings[key_a], ratings[key_b]
            expected_a = 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))
            expected_b = 1.0 - expected_a

            placement_a = placements.get(pilots[0].name)
            score_a = 1.0 if placement_a == 1 else 0.0
            score_b = 1.0 - score_a

            ratings[key_a] = rating_a + _ELO_K * (score_a - expected_a)
            ratings[key_b] = rating_b + _ELO_K * (score_b - expected_b)

        after = {key: round(ratings[key]) for key in pilot_keys}
        per_game.append(
            {
                "id": game["id"],
                "players": [
                    {"key": key, "ratingBefore": before[key], "ratingAfter": after[key]}
                    for key in pilot_keys
                ],
            }
        )

    final: dict[str, int] = {}
    for key, rating in ratings.items():
        final[key] = round(rating)
    return final, per_game
