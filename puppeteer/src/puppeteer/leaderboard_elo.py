"""Elo, placement, and model-entry helpers for leaderboard generation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from puppeteer.leaderboard_common import load_game_file
from schemas.game_export_types import Player

_LOST_GAME_RE = re.compile(r"^(.+?) has lost the game\.$")

_ELO_START = 1600
_ELO_K = 32


@dataclass(frozen=True, slots=True)
class _ModelEntry:
    model_id: str
    model_name: str
    provider: str
    rating: int | None
    games_played: int
    win_rate: float
    timeout_losses: int
    timeout_loss_rate: float
    avg_api_cost: float
    avg_tool_calls_ok: float
    avg_tool_calls_failed: float
    avg_thinking_time_secs: float
    blunder_score: float
    reasoning_effort: str | None = None

    def to_dict(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "modelId": self.model_id,
            "modelName": self.model_name,
            "provider": self.provider,
            "rating": self.rating,
            "gamesPlayed": self.games_played,
            "winRate": self.win_rate,
            "timeoutLosses": self.timeout_losses,
            "timeoutLossRate": self.timeout_loss_rate,
            "avgApiCost": self.avg_api_cost,
            "avgToolCallsOk": self.avg_tool_calls_ok,
            "avgToolCallsFailed": self.avg_tool_calls_failed,
            "avgThinkingTimeSecs": self.avg_thinking_time_secs,
            "blunderScore": self.blunder_score,
        }
        if self.reasoning_effort is not None:
            entry["reasoningEffort"] = self.reasoning_effort
        return entry


def _serialize_model_entries(models: list[_ModelEntry]) -> list[dict[str, Any]]:
    return [model.to_dict() for model in models]


def _reasoning_effort_sort_key(model: _ModelEntry) -> tuple[str, ...]:
    if model.reasoning_effort is None:
        return ()
    return (model.reasoning_effort,)


def _rated_sort_key(model: _ModelEntry) -> tuple[int, int, str, tuple[str, ...]]:
    assert model.rating is not None
    return (-model.rating, -model.games_played, model.model_id, _reasoning_effort_sort_key(model))


def _exhibition_sort_key(model: _ModelEntry) -> tuple[float, int, str, tuple[str, ...]]:
    return (-model.win_rate, -model.games_played, model.model_id, _reasoning_effort_sort_key(model))


def _player_key(model: str, reasoning_effort: str | None = None) -> str:
    """Build aggregation key: 'model_id::effort' or just 'model_id'."""
    if reasoning_effort:
        return f"{model}::{reasoning_effort}"
    return model


def _split_key(key: str) -> tuple[str, str | None]:
    """Split aggregation key into (model_id, reasoning_effort)."""
    if "::" in key:
        model_id, effort = key.split("::", 1)
        return model_id, effort
    return key, None


def extract_placements(game: Mapping[str, object], games_dir: Path | None = None) -> dict[str, int]:
    """Extract player placements from game data."""
    players_obj = game["players"]
    assert isinstance(players_obj, list), f"game {game.get('id', '<unknown>')}: players must be a list"
    players: list[Player] = []
    for index, player in enumerate(players_obj):
        assert isinstance(player, Player), f"game {game.get('id', '<unknown>')}: players[{index}] must be a Player"
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
    assert isinstance(players_obj, list), f"game {game.get('id', '<unknown>')}: players must be a list"
    for index, player in enumerate(players_obj):
        assert isinstance(player, Player), f"game {game.get('id', '<unknown>')}: players[{index}] must be a Player"
        placements[player.name] = 1 if player.name == winner else 2
    return placements


def compute_elo_ratings(
    games_index: list[dict[str, Any]],
    games_dir: Path | None = None,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Compute Elo ratings from 1v1 game history."""
    ratings: dict[str, float] = {}
    per_game: list[dict[str, Any]] = []

    sorted_games = sorted(games_index, key=lambda game: game["timestamp"] if "timestamp" in game else "")

    for game in sorted_games:
        pilots = [
            player
            for player in game["players"]
            if isinstance(player, Player) and player.type == "pilot" and player.model
        ]
        if len(pilots) < 2:
            for pilot in pilots:
                assert isinstance(pilot.model, str) and pilot.model
                key = _player_key(pilot.model, pilot.reasoningEffort)
                if key not in ratings:
                    ratings[key] = float(_ELO_START)
            if pilots:
                assert isinstance(pilots[0].model, str) and pilots[0].model
                key = _player_key(pilots[0].model, pilots[0].reasoningEffort)
                per_game.append(
                    {
                        "id": game["id"],
                        "players": [
                            {"key": key, "ratingBefore": round(ratings[key]), "ratingAfter": round(ratings[key])}
                        ],
                    }
                )
            continue

        for pilot in pilots:
            assert isinstance(pilot.model, str) and pilot.model
            key = _player_key(pilot.model, pilot.reasoningEffort)
            if key not in ratings:
                ratings[key] = float(_ELO_START)

        pilot_keys: list[str] = []
        for pilot in pilots:
            assert isinstance(pilot.model, str) and pilot.model
            pilot_keys.append(_player_key(pilot.model, pilot.reasoningEffort))

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
                "players": [{"key": key, "ratingBefore": before[key], "ratingAfter": after[key]} for key in pilot_keys],
            }
        )

    final: dict[str, int] = {}
    for key, rating in ratings.items():
        final[key] = round(rating)
    return final, per_game
