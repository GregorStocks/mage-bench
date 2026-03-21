"""Generate leaderboard data from game results using Elo ratings."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from puppeteer.harness_epoch import MIN_BLUNDER_VERSION
from puppeteer.leaderboard_common import (
    glob_game_files as _glob_game_files,
)
from puppeteer.leaderboard_common import (
    load_game_file as _load_game_file,
)
from puppeteer.leaderboard_common import (
    write_if_changed as _write_if_changed,
)
from puppeteer.leaderboard_elo import (
    _ELO_START,
    _exhibition_sort_key,
    _ModelEntry,
    _player_key,
    _rated_sort_key,
    _serialize_model_entries,
    _split_key,
    compute_elo_ratings,
    extract_placements,
)
from puppeteer.leaderboard_formats import (
    EXHIBITION_POOLS as _EXHIBITION_POOLS,
)
from puppeteer.leaderboard_formats import (
    FORMAT_LABELS,
    derive_format,
)
from puppeteer.leaderboard_formats import (
    FORMAT_POOLS as _FORMAT_POOLS,
)
from puppeteer.leaderboard_formats import (
    RATED_POOLS as _RATED_POOLS,
)
from puppeteer.leaderboard_registry import (
    _load_inactive_statuses,
    capitalize_provider,
    derive_display_name,
    load_model_registry,
)
from puppeteer.leaderboard_stats import (
    generate_blunder_stats,
    generate_internals_data,
    generate_model_stats,
)

__all__ = [
    "BLUNDER_WEIGHTS",
    "FORMAT_LABELS",
    "_player_key",
    "_split_key",
    "capitalize_provider",
    "compute_elo_ratings",
    "compute_thinking_time",
    "derive_display_name",
    "derive_format",
    "extract_placements",
    "generate_all_leaderboards",
    "generate_blunder_stats",
    "generate_exhibition_leaderboard",
    "generate_internals_data",
    "generate_leaderboard",
    "generate_leaderboard_file",
    "generate_model_stats",
    "load_model_registry",
]

# Severity weights for blunder index. Higher weight = worse blunder.
# Questionable moves are excluded; they're tracked but do not count.
BLUNDER_WEIGHTS: dict[str, int] = {
    "minor": 1,
    "moderate": 2,
    "major": 4,
}


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
    final_ratings, per_game = compute_elo_ratings(scored_games, games_dir)

    ratings_by_game: dict[str, dict[str, dict[str, int]]] = {}
    for game_entry in per_game:
        game_id = game_entry["id"]
        ratings_by_game[game_id] = {
            player["key"]: {"before": player["ratingBefore"], "after": player["ratingAfter"]}
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

        total_turns = game.get("totalTurns", 0)
        for player in game["players"]:
            if player.type != "pilot" or not player.model:
                continue
            key = _player_key(player.model, player.reasoningEffort)
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
            if player.timedOut:
                stats[key]["timeout_losses"] += 1
            stats[key]["total_cost"] += player.totalCostUsd or 0.0
            stats[key]["total_tool_calls_ok"] += player.toolCallsOk
            stats[key]["total_tool_calls_failed"] += player.toolCallsFailed
            stats[key]["total_thinking_time"] += player.thinkingTimeSecs
            assert annotations is not None, f"Game {game.get('id')} has no annotations"
            assert total_turns > 0, f"Game {game.get('id')} has no turns"
            stats[key]["total_annotated_turns"] += total_turns
            stats[key]["total_weighted_blunders"] += blunder_weight_by_name.get(player.name, 0)

    models: list[_ModelEntry] = []
    for key, stat in stats.items():
        model_id, effort = _split_key(key)
        games_played = int(stat["games_played"])
        wins = int(stat["wins"])
        provider_slug = model_id.split("/", 1)[0]
        total_annotated_turns = int(stat["total_annotated_turns"])
        assert total_annotated_turns > 0, f"Model {model_id} has no annotated turns"

        display_name = model_registry.get(model_id) or derive_display_name(model_id)
        if effort:
            display_name = f"{display_name} ({effort})"

        timeout_losses = int(stat["timeout_losses"])
        models.append(
            _ModelEntry(
                model_id=model_id,
                model_name=display_name,
                provider=capitalize_provider(provider_slug),
                rating=final_ratings.get(key, _ELO_START),
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

        total_turns = game.get("totalTurns", 0)
        for player in game["players"]:
            if player.type != "pilot" or not player.model:
                continue
            key = _player_key(player.model, player.reasoningEffort)
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
            if player.timedOut:
                stats[key]["timeout_losses"] += 1
            stats[key]["total_cost"] += player.totalCostUsd or 0.0
            stats[key]["total_tool_calls_ok"] += player.toolCallsOk
            stats[key]["total_tool_calls_failed"] += player.toolCallsFailed
            stats[key]["total_thinking_time"] += player.thinkingTimeSecs
            assert annotations is not None, f"Game {game.get('id')} has no annotations"
            assert total_turns > 0, f"Game {game.get('id')} has no turns"
            stats[key]["total_annotated_turns"] += total_turns
            stats[key]["total_weighted_blunders"] += blunder_weight_by_name.get(player.name, 0)

    models: list[_ModelEntry] = []
    for key, stat in stats.items():
        model_id, effort = _split_key(key)
        games_played = int(stat["games_played"])
        wins = int(stat["wins"])
        provider_slug = model_id.split("/", 1)[0]
        total_annotated_turns = int(stat["total_annotated_turns"])
        assert total_annotated_turns > 0, f"Model {model_id} has no annotated turns"

        display_name = model_registry.get(model_id) or derive_display_name(model_id)
        if effort:
            display_name = f"{display_name} ({effort})"

        timeout_losses = int(stat["timeout_losses"])
        models.append(
            _ModelEntry(
                model_id=model_id,
                model_name=display_name,
                provider=capitalize_provider(provider_slug),
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
    games_by_format: dict[str, list[dict[str, Any]]] = {fmt: [] for fmt in _FORMAT_POOLS}
    for game in games_index:
        game_format = derive_format(game)
        if game_format in games_by_format:
            games_by_format[game_format].append(game)

    format_results: dict[str, dict[str, Any]] = {}
    ratings_by_game: dict[str, dict[str, dict[str, int]]] = {}

    for game_format in _RATED_POOLS:
        results, ratings = generate_leaderboard(games_by_format[game_format], model_registry, games_dir)
        format_results[game_format] = results
        ratings_by_game.update(ratings)

    for game_format in _EXHIBITION_POOLS:
        format_results[game_format] = generate_exhibition_leaderboard(games_by_format[game_format], model_registry)

    rated_games = [game for game_format in _RATED_POOLS for game in games_by_format[game_format]]
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
            "gameType": game_export.gameType,
            "deckType": game_export.deckType,
            "totalTurns": game_export.totalTurns,
            "winner": game_export.winner,
            "players": game_export.players,
            "harnessEpoch": game_export.harnessEpoch,
            "season": game_export.season,
        }
        if game_export.annotations is not None:
            game_entry["annotations"] = game_export.annotations
        if game_export.tournament:
            game_entry["tournament"] = True
        games_index.append(game_entry)

    model_registry = load_model_registry(models_json)
    inactive_statuses = _load_inactive_statuses(models_json.parent / "presets.json")

    def _mark_inactive(format_results: dict[str, Any]) -> None:
        if inactive_statuses is None:
            return
        for format_data in format_results.values():
            for model in format_data["models"]:
                model_id = model["modelId"]
                effort = model.get("reasoningEffort")
                key = f"{model_id}::{effort}" if effort else model_id
                if key in inactive_statuses:
                    model["inactive"] = inactive_statuses[key]

    def _build_output(season_games: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        format_results, ratings = generate_all_leaderboards(season_games, model_registry, games_dir)
        _mark_inactive(format_results)
        combined_pool = format_results["combined"]
        total_games = sum(
            format_results[game_format]["totalGames"] for game_format in _FORMAT_POOLS if game_format in format_results
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
