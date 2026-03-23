"""Generate leaderboard data from game results using Elo ratings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import magebench.leaderboard.elo as _elo
import magebench.leaderboard.formats as _formats
import magebench.leaderboard.registry as _registry
from magebench.leaderboard.common import (
    glob_game_files as _glob_game_files,
)
from magebench.leaderboard.common import (
    load_game_file as _load_game_file,
)
from magebench.leaderboard.common import (
    write_if_changed as _write_if_changed,
)

# Severity weights for blunder index. Higher weight = worse blunder.
# Questionable moves are excluded; they're tracked but do not count.
BLUNDER_WEIGHTS: dict[str, int] = {
    "minor": 1,
    "moderate": 2,
    "major": 4,
}

# Minimum blunder analysis version for "acceptable" annotations. Games
# analyzed below this show an "(older analysis)" tag on the website.
# (See BLUNDER_SCRIPT_VERSION in magebench.analysis.blunder.blunder_analysis.)
MIN_BLUNDER_VERSION = 11


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
    return (
        -model.rating,
        -model.games_played,
        model.model_id,
        _reasoning_effort_sort_key(model),
    )


def _exhibition_sort_key(model: _ModelEntry) -> tuple[float, int, str, tuple[str, ...]]:
    return (
        -model.win_rate,
        -model.games_played,
        model.model_id,
        _reasoning_effort_sort_key(model),
    )


def compute_thinking_time(llm_events: list[dict]) -> dict[str, float]:
    """Compute per-player thinking time from sorted LLM events."""
    thinking: dict[str, float] = {}
    for index in range(len(llm_events) - 1):
        player = llm_events[index].get("player")
        if not player:
            continue
        ts_a = llm_events[index].get("ts")
        ts_b = llm_events[index + 1].get("ts")
        if not ts_a or not ts_b:
            continue
        try:
            dt_a = datetime.fromisoformat(ts_a)
            dt_b = datetime.fromisoformat(ts_b)
        except ValueError:
            continue
        gap = (dt_b - dt_a).total_seconds()
        if gap > 0:
            thinking[player] = thinking.get(player, 0.0) + gap
    return thinking


def generate_leaderboard(
    games_index: list[dict[str, Any]],
    model_registry: dict[str, str],
    games_dir: Path | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, dict[str, int]]]]:
    """Aggregate game results into leaderboard data."""
    scored_games = [game for game in games_index if game.get("winner") and not game.get("tournament")]
    final_ratings, per_game = _elo.compute_elo_ratings(scored_games, games_dir)

    ratings_by_game: dict[str, dict[str, dict[str, int]]] = {}
    for game_entry in per_game:
        game_id = game_entry["id"]
        ratings_by_game[game_id] = {
            player["key"]: {
                "before": player["ratingBefore"],
                "after": player["ratingAfter"],
            }
            for player in game_entry["players"]
        }

    stats: dict[str, dict[str, float]] = {}
    for game in scored_games:
        blunder_weight_by_name: dict[str, float] = {}
        annotations = game.get("annotations")
        if annotations is not None:
            for annotation in annotations:
                if annotation.type != "blunder" or not annotation.player:
                    continue
                blunder_weight_by_name[annotation.player] = blunder_weight_by_name.get(annotation.player, 0) + (
                    BLUNDER_WEIGHTS.get(annotation.severity, 0)
                )

        total_turns = game.get("total_turns", 0)
        for player in game["players"]:
            if player.type != "pilot" or not player.model:
                continue
            key = _elo.player_key(player.model, player.reasoning_effort)
            if key not in stats:
                stats[key] = {
                    "games_played": 0,
                    "wins": 0,
                    "timeout_losses": 0,
                    "total_cost": 0.0,
                    "total_tool_calls_ok": 0,
                    "total_tool_calls_failed": 0,
                    "total_thinking_time": 0.0,
                    "total_weighted_blunders": 0.0,
                    "total_annotated_turns": 0,
                }
            stats[key]["games_played"] += 1
            if game.get("winner") == player.name:
                stats[key]["wins"] += 1
            if player.timed_out:
                stats[key]["timeout_losses"] += 1
            stats[key]["total_cost"] += player.total_cost_usd or 0.0
            stats[key]["total_tool_calls_ok"] += player.tool_calls_ok
            stats[key]["total_tool_calls_failed"] += player.tool_calls_failed
            stats[key]["total_thinking_time"] += player.thinking_time_secs
            assert annotations is not None, f"Game {game.get('id')} has no annotations"
            assert total_turns > 0, f"Game {game.get('id')} has no turns"
            stats[key]["total_annotated_turns"] += total_turns
            stats[key]["total_weighted_blunders"] += blunder_weight_by_name.get(player.name, 0)

    models: list[_ModelEntry] = []
    for key, stat in stats.items():
        model_id, effort = _elo.split_key(key)
        games_played = int(stat["games_played"])
        wins = int(stat["wins"])
        provider_slug = model_id.split("/", 1)[0]
        total_annotated_turns = int(stat["total_annotated_turns"])
        assert total_annotated_turns > 0, f"Model {model_id} has no annotated turns"
        assert key in final_ratings, f"Missing final rating for {key}"

        display_name = model_registry.get(model_id) or _registry.derive_display_name(model_id)
        if effort:
            display_name = f"{display_name} ({effort})"

        timeout_losses = int(stat["timeout_losses"])
        models.append(
            _ModelEntry(
                model_id=model_id,
                model_name=display_name,
                provider=_registry.capitalize_provider(provider_slug),
                rating=final_ratings[key],
                games_played=games_played,
                win_rate=round(wins / games_played, 4),
                timeout_losses=timeout_losses,
                timeout_loss_rate=round(timeout_losses / games_played, 4),
                avg_api_cost=round(stat["total_cost"] / games_played, 2),
                avg_tool_calls_ok=round(stat["total_tool_calls_ok"] / games_played, 1),
                avg_tool_calls_failed=round(stat["total_tool_calls_failed"] / games_played, 1),
                avg_thinking_time_secs=round(stat["total_thinking_time"] / games_played, 1),
                blunder_score=round(stat["total_weighted_blunders"] / total_annotated_turns, 2),
                reasoning_effort=effort,
            )
        )

    models.sort(key=_rated_sort_key)
    return {
        "generatedAt": datetime.now(UTC).isoformat(),
        "totalGames": len(scored_games),
        "models": _serialize_model_entries(models),
    }, ratings_by_game


def generate_exhibition_leaderboard(
    games_index: list[dict[str, Any]],
    model_registry: dict[str, str],
) -> dict[str, Any]:
    """Aggregate exhibition game results into stats-only leaderboard."""
    scored_games = [game for game in games_index if game.get("winner")]

    stats: dict[str, dict[str, float]] = {}
    for game in scored_games:
        blunder_weight_by_name: dict[str, float] = {}
        annotations = game.get("annotations")
        if annotations is not None:
            for annotation in annotations:
                if annotation.type != "blunder" or not annotation.player:
                    continue
                blunder_weight_by_name[annotation.player] = blunder_weight_by_name.get(annotation.player, 0) + (
                    BLUNDER_WEIGHTS.get(annotation.severity, 0)
                )

        total_turns = game.get("total_turns", 0)
        for player in game["players"]:
            if player.type != "pilot" or not player.model:
                continue
            key = _elo.player_key(player.model, player.reasoning_effort)
            if key not in stats:
                stats[key] = {
                    "games_played": 0,
                    "wins": 0,
                    "timeout_losses": 0,
                    "total_cost": 0.0,
                    "total_tool_calls_ok": 0,
                    "total_tool_calls_failed": 0,
                    "total_thinking_time": 0.0,
                    "total_weighted_blunders": 0.0,
                    "total_annotated_turns": 0,
                }
            stats[key]["games_played"] += 1
            if game.get("winner") == player.name:
                stats[key]["wins"] += 1
            if player.timed_out:
                stats[key]["timeout_losses"] += 1
            stats[key]["total_cost"] += player.total_cost_usd or 0.0
            stats[key]["total_tool_calls_ok"] += player.tool_calls_ok
            stats[key]["total_tool_calls_failed"] += player.tool_calls_failed
            stats[key]["total_thinking_time"] += player.thinking_time_secs
            assert annotations is not None, f"Game {game.get('id')} has no annotations"
            assert total_turns > 0, f"Game {game.get('id')} has no turns"
            stats[key]["total_annotated_turns"] += total_turns
            stats[key]["total_weighted_blunders"] += blunder_weight_by_name.get(player.name, 0)

    models: list[_ModelEntry] = []
    for key, stat in stats.items():
        model_id, effort = _elo.split_key(key)
        games_played = int(stat["games_played"])
        wins = int(stat["wins"])
        provider_slug = model_id.split("/", 1)[0]
        total_annotated_turns = int(stat["total_annotated_turns"])
        assert total_annotated_turns > 0, f"Model {model_id} has no annotated turns"

        display_name = model_registry.get(model_id) or _registry.derive_display_name(model_id)
        if effort:
            display_name = f"{display_name} ({effort})"

        timeout_losses = int(stat["timeout_losses"])
        models.append(
            _ModelEntry(
                model_id=model_id,
                model_name=display_name,
                provider=_registry.capitalize_provider(provider_slug),
                rating=None,
                games_played=games_played,
                win_rate=round(wins / games_played, 4),
                timeout_losses=timeout_losses,
                timeout_loss_rate=round(timeout_losses / games_played, 4),
                avg_api_cost=round(stat["total_cost"] / games_played, 2),
                avg_tool_calls_ok=round(stat["total_tool_calls_ok"] / games_played, 1),
                avg_tool_calls_failed=round(stat["total_tool_calls_failed"] / games_played, 1),
                avg_thinking_time_secs=round(stat["total_thinking_time"] / games_played, 1),
                blunder_score=round(stat["total_weighted_blunders"] / total_annotated_turns, 2),
                reasoning_effort=effort,
            )
        )

    models.sort(key=_exhibition_sort_key)
    return {
        "generatedAt": datetime.now(UTC).isoformat(),
        "totalGames": len(scored_games),
        "exhibition": True,
        "models": _serialize_model_entries(models),
    }


def generate_all_leaderboards(
    games_index: list[dict[str, Any]],
    model_registry: dict[str, str],
    games_dir: Path | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, dict[str, int]]]]:
    """Generate per-format leaderboards plus a combined view."""
    games_by_format: dict[str, list[dict[str, Any]]] = {fmt: [] for fmt in _formats.FORMAT_POOLS}
    for game in games_index:
        game_format = _formats.derive_format(game)
        if game_format in games_by_format:
            games_by_format[game_format].append(game)

    format_results: dict[str, dict[str, Any]] = {}
    ratings_by_game: dict[str, dict[str, dict[str, int]]] = {}

    for game_format in _formats.RATED_POOLS:
        results, ratings = generate_leaderboard(games_by_format[game_format], model_registry, games_dir)
        format_results[game_format] = results
        ratings_by_game.update(ratings)

    for game_format in _formats.EXHIBITION_POOLS:
        format_results[game_format] = generate_exhibition_leaderboard(games_by_format[game_format], model_registry)

    rated_games = [game for game_format in _formats.RATED_POOLS for game in games_by_format[game_format]]
    combined_results, _ = generate_leaderboard(rated_games, model_registry, games_dir)
    format_results["combined"] = combined_results

    return format_results, ratings_by_game


def generate_leaderboard_file(
    games_dir: Path,
    data_dir: Path,
    models_json: Path,
    current_season: int | None = None,
) -> Path:
    """Generate leaderboard files from game data."""
    games_index: list[dict[str, Any]] = []
    for game_path in _glob_game_files(games_dir):
        game_export = _load_game_file(game_path)
        game_entry: dict[str, Any] = {
            "id": game_export.id,
            "timestamp": game_export.timestamp,
            "game_type": game_export.game_type,
            "deck_type": game_export.deck_type,
            "total_turns": game_export.total_turns,
            "winner": game_export.winner,
            "players": game_export.players,
            "harness_epoch": game_export.harness_epoch,
            "season": game_export.season,
        }
        if game_export.annotations is not None:
            game_entry["annotations"] = game_export.annotations
        if game_export.tournament:
            game_entry["tournament"] = True
        games_index.append(game_entry)

    model_registry = _registry.load_model_registry(models_json)
    inactive_statuses = _registry.load_inactive_statuses(models_json.parent / "presets.json")

    def _mark_inactive(format_results: dict[str, Any]) -> None:
        if inactive_statuses is None:
            return
        for format_data in format_results.values():
            for model in format_data["models"]:
                model_id = model["modelId"]
                effort = model.get("reasoningEffort")
                key = _elo.player_key(model_id, effort)
                if key in inactive_statuses:
                    model["inactive"] = inactive_statuses[key]

    def _build_output(
        season_games: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        format_results, ratings = generate_all_leaderboards(season_games, model_registry, games_dir)
        _mark_inactive(format_results)
        combined_pool = format_results["combined"]
        total_games = sum(
            format_results[game_format]["totalGames"]
            for game_format in _formats.FORMAT_POOLS
            if game_format in format_results
        )
        return {
            "generatedAt": combined_pool["generatedAt"],
            "totalGames": total_games,
            "models": combined_pool["models"],
            "formats": format_results,
            "minBlunderVersion": MIN_BLUNDER_VERSION,
        }, ratings

    games_by_season: dict[int, list[dict[str, Any]]] = {}
    for game_entry in games_index:
        games_by_season.setdefault(game_entry["season"], []).append(game_entry)

    data_dir.mkdir(parents=True, exist_ok=True)
    all_ratings: dict[str, Any] = {}
    available_seasons: list[int] = sorted(games_by_season.keys())
    if current_season is not None and current_season not in available_seasons:
        available_seasons.append(current_season)
        available_seasons.sort()

    public_data_dir = games_dir.parent / "data"
    public_data_dir.mkdir(parents=True, exist_ok=True)

    for season_num in available_seasons:
        season_games = games_by_season.get(season_num)
        season_output, season_ratings = _build_output(season_games if season_games is not None else [])
        season_output["availableSeasons"] = available_seasons
        season_path = public_data_dir / f"benchmark-results-season-{season_num}.json"
        _write_if_changed(season_path, json.dumps(season_output, indent=2) + "\n")
        all_ratings.update(season_ratings)

    rated_seasons = [season for season in available_seasons if season >= 1]
    if len(rated_seasons) == 1 and rated_seasons[0] in games_by_season:
        output, ratings_by_game = _build_output(games_by_season[rated_seasons[0]])
    else:
        output, ratings_by_game = _build_output([game for game in games_index if game["season"] >= 1])
    all_ratings.update(ratings_by_game)
    output["availableSeasons"] = available_seasons
    output_path = data_dir / "benchmark-results.json"
    _write_if_changed(output_path, json.dumps(output, indent=2) + "\n")

    ratings_path = public_data_dir / "ratings.json"
    _write_if_changed(ratings_path, json.dumps(all_ratings, indent=2) + "\n")

    return output_path
