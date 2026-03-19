"""Generate leaderboard data from game results using Elo ratings."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from puppeteer.harness_epoch import MIN_BLUNDER_VERSION
from schemas.game_export_types import (
    BuiltGameExport,
    GameExport,
    LlmErrorEvent,
    LlmResponseEvent,
    Player,
    is_pilot_player,
    load_game_export,
)

_GENERATED_AT_RE = re.compile(r'"generatedAt":\s*"[^"]*",?\n?')
_GAME_TIMESTAMP_TZ = ZoneInfo("America/Los_Angeles")


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


_LOST_GAME_RE = re.compile(r"^(.+?) has lost the game\.$")


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


def _write_if_changed(path: Path, content: str) -> bool:
    """Write content to path only if something besides generatedAt changed.

    Compares the new content against the existing file, stripping the
    top-level "generatedAt" value from both before comparing.  Returns
    True if the file was written, False if skipped.
    """
    try:
        existing = path.read_text()
    except FileNotFoundError:
        path.write_text(content)
        return True
    if existing == content:
        return False
    if _GENERATED_AT_RE.sub("", existing) == _GENERATED_AT_RE.sub("", content):
        return False
    path.write_text(content)
    return True


def _load_game_file(path: Path) -> GameExport:
    """Load a game export file (.json or .json.gz)."""
    return load_game_export(path)


def _assert_int(value: object, message: str) -> None:
    assert isinstance(value, int) and not isinstance(value, bool), message


def _assert_number(value: object, message: str) -> None:
    assert isinstance(value, (int, float)) and not isinstance(value, bool), message


def _glob_game_files(games_dir: Path) -> list[Path]:
    """Find all game export files (.json and .json.gz) in a directory, sorted."""
    gz_files = set(games_dir.glob("game_*.json.gz"))
    gz_stems = {p.name.removesuffix(".gz") for p in gz_files}
    json_files = [p for p in games_dir.glob("game_*.json") if p.name not in gz_stems]
    return sorted(gz_files | set(json_files))


# Severity weights for blunder index. Higher weight = worse blunder.
# Questionable moves are excluded — they're tracked but don't count.
BLUNDER_WEIGHTS: dict[str, int] = {
    "minor": 1,
    "moderate": 2,
    "major": 4,
}


def compute_thinking_time(llm_events: list[dict]) -> dict[str, float]:
    """Compute per-player thinking time from sorted LLM events.

    For each consecutive pair of events, the time gap is attributed to the
    player of the earlier event. This approximates wall-clock time each player
    spent with priority (similar to a chess clock).

    Returns {player_name: total_seconds}.
    """
    thinking: dict[str, float] = {}
    for i in range(len(llm_events) - 1):
        player = llm_events[i].get("player")
        if not player:
            continue
        ts_a = llm_events[i].get("ts")
        ts_b = llm_events[i + 1].get("ts")
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


# Elo rating parameters.
_ELO_START = 1600
_ELO_K = 32

# Map XMage deckType strings to canonical format names for leaderboard bucketing.
_DECK_TYPE_TO_FORMAT: dict[str, str] = {
    "Constructed - Standard": "standard",
    "Constructed - Modern": "modern",
    "Constructed - Legacy": "legacy",
    "Variant Magic - Freeform Commander": "commander",
    "Variant Magic - Commander": "commander",
    "Limited": "jumpstart",
}

# Display labels for leaderboard tabs.
FORMAT_LABELS: dict[str, str] = {
    "jumpstart": "Jumpstart",
    "standard": "Standard",
    "modern": "Modern",
    "legacy": "Legacy",
    "commander": "Commander (Exhibition)",
    "combined": "Combined",
}


def derive_format(game: Mapping[str, object] | GameExport | BuiltGameExport) -> str:
    """Derive canonical format name from game data.

    Requires deckType to be present on the normalized export shape.
    """
    if isinstance(game, (GameExport, BuiltGameExport)):
        deck_type: object = game.deckType
        game_id: object = game.id
    else:
        deck_type = game.get("deckType")
        game_id = game.get("id", "<unknown>")
    assert isinstance(deck_type, str) and deck_type, f"Game {game_id} missing deckType"
    if deck_type in _DECK_TYPE_TO_FORMAT:
        return _DECK_TYPE_TO_FORMAT[deck_type]
    return deck_type.lower().replace(" ", "-")


_PROVIDER_DISPLAY: dict[str, str] = {
    "anthropic": "Anthropic",
    "google": "Google",
    "openai": "OpenAI",
    "mistralai": "Mistral AI",
    "deepseek": "DeepSeek",
    "meta-llama": "Meta",
    "x-ai": "xAI",
}


def capitalize_provider(slug: str) -> str:
    """Convert provider slug to display name."""
    return _PROVIDER_DISPLAY.get(slug, slug.title())


def derive_display_name(model_id: str) -> str:
    """Derive a display name from a model ID not in the registry.

    Takes the part after '/' and title-cases it with spaces.
    E.g. "mistralai/devstral-small" -> "Devstral Small"
    """
    slug = model_id.split("/", 1)[-1]
    return slug.replace("-", " ").title()


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


def load_model_registry(models_json: Path) -> dict[str, str]:
    """Load model ID -> display name mapping from models.json."""
    if not models_json.exists():
        return {}
    data = json.loads(models_json.read_text())
    assert isinstance(data, dict), f"{models_json}: expected JSON object"
    models = data["models"]
    assert isinstance(models, list), f"{models_json}: models must be a list"
    registry: dict[str, str] = {}
    for index, model in enumerate(models):
        assert isinstance(model, dict), f"{models_json}: models[{index}] must be an object"
        model_id = model.get("id")
        model_name = model.get("name")
        assert isinstance(model_id, str) and model_id, f"{models_json}: models[{index}] missing id"
        assert isinstance(model_name, str) and model_name, f"{models_json}: models[{index}] missing name"
        registry[model_id] = model_name
    return registry


def _load_inactive_statuses(presets_json: Path) -> dict[str, str] | None:
    """Load inactive statuses for non-active presets from presets.json.

    Returns a dict mapping player_key -> status (e.g. "retired", "buggy", "expensive")
    for presets whose status is not "active".
    Returns None if presets.json doesn't exist (e.g. in tests).
    """
    if not presets_json.exists():
        return None
    data = json.loads(presets_json.read_text())
    presets = data["presets"]
    statuses: dict[str, str] = {}
    for preset in presets.values():
        status = preset.get("status", "retired")
        if status == "active":
            continue
        model_id = preset["model"]
        effort = preset.get("reasoning_effort")
        key = f"{model_id}::{effort}" if effort else model_id
        statuses[key] = status
    return statuses


def extract_placements(game: Mapping[str, object], games_dir: Path | None = None) -> dict[str, int]:
    """Extract player placements from game data.

    Uses the 'placement' field if present on players. Otherwise, falls back
    to parsing "X has lost the game." messages from the full game JSON file
    (if games_dir is provided).

    Returns {player_name: placement} where 1=winner, 2=2nd, etc.
    """
    players_obj = game["players"]
    assert isinstance(players_obj, list), f"game {game.get('id', '<unknown>')}: players must be a list"
    players: list[Player] = []
    for index, player in enumerate(players_obj):
        assert isinstance(player, Player), f"game {game.get('id', '<unknown>')}: players[{index}] must be a Player"
        players.append(player)

    # Check if placements are already in the index data
    if any(p.placement is not None for p in players):
        existing_placements: dict[str, int] = {}
        for player in players:
            if player.placement is None:
                continue
            existing_placements[player.name] = player.placement
        return existing_placements

    # Fall back to reading actions from the full game JSON
    if games_dir is None:
        return _placements_from_winner(game)

    game_path = games_dir / f"{game['id']}.json.gz"
    if not game_path.exists():
        game_path = games_dir / f"{game['id']}.json"
    if not game_path.exists():
        return _placements_from_winner(game)

    full_game = _load_game_file(game_path)
    actions = full_game.actions
    player_names: list[str] = [p.name for p in players]
    winner = game.get("winner")
    assert winner is None or isinstance(winner, str), (
        f"game {game.get('id', '<unknown>')}: winner must be a string or null"
    )

    eliminations = []
    for a in actions:
        msg = a.message
        m = _LOST_GAME_RE.match(msg) if msg else None
        if m:
            eliminations.append(m.group(1))

    placements: dict[str, int] = {}
    if winner:
        placements[winner] = 1
        for i, name in enumerate(reversed(eliminations)):
            placements[name] = i + 2
    elif eliminations:
        surviving = [n for n in player_names if n not in eliminations]
        for name in surviving:
            placements[name] = 1
        for i, name in enumerate(reversed(eliminations)):
            placements[name] = len(surviving) + i + 1

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
    for index, p in enumerate(players_obj):
        assert isinstance(p, Player), f"game {game.get('id', '<unknown>')}: players[{index}] must be a Player"
        placements[p.name] = 1 if p.name == winner else 2
    return placements


def compute_elo_ratings(
    games_index: list[dict],
    games_dir: Path | None = None,
) -> tuple[dict[str, int], list[dict]]:
    """Compute Elo ratings from 1v1 game history.

    Standard Elo with K=32. Games with fewer than 2 pilots or no placement
    data are skipped (no rating update) but still record snapshots.

    Returns (final_ratings, per_game_ratings).
    """
    ratings: dict[str, float] = {}
    per_game: list[dict] = []

    sorted_games = sorted(
        games_index,
        key=lambda g: g["timestamp"] if "timestamp" in g else "",
    )

    for game in sorted_games:
        pilots = [p for p in game["players"] if isinstance(p, Player) and p.type == "pilot" and p.model]
        if len(pilots) < 2:
            for p in pilots:
                key = _player_key(p.model, p.reasoningEffort)  # type: ignore[arg-type]
                if key not in ratings:
                    ratings[key] = float(_ELO_START)
            if pilots:
                key = _player_key(pilots[0].model, pilots[0].reasoningEffort)  # type: ignore[arg-type]
                per_game.append(
                    {
                        "id": game["id"],
                        "players": [
                            {"key": key, "ratingBefore": round(ratings[key]), "ratingAfter": round(ratings[key])}
                        ],
                    }
                )
            continue

        for p in pilots:
            key = _player_key(p.model, p.reasoningEffort)  # type: ignore[arg-type]
            if key not in ratings:
                ratings[key] = float(_ELO_START)

        pilot_keys = [_player_key(p.model, p.reasoningEffort) for p in pilots]  # type: ignore[arg-type]
        before = {key: round(ratings[key]) for key in pilot_keys}

        placements = extract_placements(game, games_dir)
        has_placements = any(p.name in placements for p in pilots)

        if has_placements and len(pilots) == 2:
            key_a, key_b = pilot_keys
            ra, rb = ratings[key_a], ratings[key_b]
            ea = 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))
            eb = 1.0 - ea

            # Determine scores: winner gets 1, loser gets 0
            placement_a = placements.get(pilots[0].name)
            sa = 1.0 if placement_a == 1 else 0.0
            sb = 1.0 - sa

            ratings[key_a] = ra + _ELO_K * (sa - ea)
            ratings[key_b] = rb + _ELO_K * (sb - eb)

        after = {key: round(ratings[key]) for key in pilot_keys}
        per_game.append(
            {
                "id": game["id"],
                "players": [
                    {
                        "key": key,
                        "ratingBefore": before[key],
                        "ratingAfter": after[key],
                    }
                    for key in pilot_keys
                ],
            }
        )

    final: dict[str, int] = {}
    for key, r in ratings.items():
        final[key] = round(r)

    return final, per_game


def generate_leaderboard(
    games_index: list[dict],
    model_registry: dict[str, str],
    games_dir: Path | None = None,
) -> tuple[dict, dict[str, dict[str, dict[str, int]]]]:
    """Aggregate game results into leaderboard data.

    Uses Elo for ratings (1v1 games only).

    Returns (benchmark_results, ratings_by_game) where ratings_by_game is
    {game_id: {model_id: {before, after}}}.
    """
    # Filter to games with a winner for leaderboard purposes
    # Exclude tournament games — they have separate scoring
    scored_games = [g for g in games_index if g.get("winner") and not g.get("tournament")]

    final_ratings, per_game = compute_elo_ratings(scored_games, games_dir)

    # Build ratings_by_game lookup
    ratings_by_game: dict[str, dict[str, dict[str, int]]] = {}
    for game_entry in per_game:
        game_id = game_entry["id"]
        ratings_by_game[game_id] = {
            p["key"]: {"before": p["ratingBefore"], "after": p["ratingAfter"]} for p in game_entry["players"]
        }

    # Aggregate per-player-key stats (model_id::effort or just model_id)
    stats: dict[str, dict[str, float]] = {}
    for game in scored_games:
        # Build name -> weighted blunder sum from annotations.
        blunder_weight_by_name: dict[str, float] = {}
        annotations = game.get("annotations")
        if annotations is not None:
            for ann in annotations:
                ann_type, name, severity = ann.type, ann.player, ann.severity
                if ann_type == "blunder":
                    if not name:
                        continue
                    blunder_weight_by_name[name] = blunder_weight_by_name.get(name, 0) + BLUNDER_WEIGHTS.get(
                        severity, 0
                    )

        total_turns = game.get("totalTurns", 0)

        for p in game["players"]:
            if p.type != "pilot" or not p.model:
                continue
            key = _player_key(p.model, p.reasoningEffort)
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
            if game.get("winner") == p.name:
                stats[key]["wins"] += 1
            if p.timedOut:
                stats[key]["timeout_losses"] += 1
            stats[key]["total_cost"] += p.totalCostUsd or 0.0
            stats[key]["total_tool_calls_ok"] += p.toolCallsOk
            stats[key]["total_tool_calls_failed"] += p.toolCallsFailed
            stats[key]["total_thinking_time"] += p.thinkingTimeSecs
            assert game.get("annotations") is not None, f"Game {game.get('id')} has no annotations"
            assert total_turns > 0, f"Game {game.get('id')} has no turns"
            stats[key]["total_annotated_turns"] += total_turns
            stats[key]["total_weighted_blunders"] += blunder_weight_by_name.get(p.name, 0)

    # Build models list
    models: list[_ModelEntry] = []
    for key, s in stats.items():
        model_id, effort = _split_key(key)
        games_played = int(s["games_played"])
        wins = int(s["wins"])
        win_rate = wins / games_played
        avg_cost = s["total_cost"] / games_played
        provider_slug = model_id.split("/", 1)[0]
        rating = final_ratings.get(key, _ELO_START)

        display_name = model_registry.get(model_id) or derive_display_name(model_id)
        if effort:
            display_name = f"{display_name} ({effort})"

        avg_tool_calls_ok = s["total_tool_calls_ok"] / games_played
        avg_tool_calls_failed = s["total_tool_calls_failed"] / games_played
        avg_thinking_time = s["total_thinking_time"] / games_played
        total_annotated_turns = int(s["total_annotated_turns"])
        assert total_annotated_turns > 0, f"Model {model_id} has no annotated turns"
        blunder_score = s["total_weighted_blunders"] / total_annotated_turns
        timeout_losses = int(s["timeout_losses"])
        timeout_loss_rate = timeout_losses / games_played
        models.append(
            _ModelEntry(
                model_id=model_id,
                model_name=display_name,
                provider=capitalize_provider(provider_slug),
                rating=rating,
                games_played=games_played,
                win_rate=round(win_rate, 4),
                timeout_losses=timeout_losses,
                timeout_loss_rate=round(timeout_loss_rate, 4),
                avg_api_cost=round(avg_cost, 2),
                avg_tool_calls_ok=round(avg_tool_calls_ok, 1),
                avg_tool_calls_failed=round(avg_tool_calls_failed, 1),
                avg_thinking_time_secs=round(avg_thinking_time, 1),
                blunder_score=round(blunder_score, 2),
                reasoning_effort=effort,
            )
        )

    # Sort by rating desc, then games_played desc, then modelId for determinism
    models.sort(key=_rated_sort_key)

    benchmark_results = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "totalGames": len(scored_games),
        "models": _serialize_model_entries(models),
    }

    return benchmark_results, ratings_by_game


_RATED_POOLS = ("jumpstart", "standard", "modern", "legacy")
_EXHIBITION_POOLS = ("commander",)
_FORMAT_POOLS = _RATED_POOLS + _EXHIBITION_POOLS


def generate_exhibition_leaderboard(
    games_index: list[dict],
    model_registry: dict[str, str],
) -> dict:
    """Aggregate exhibition game results into stats-only leaderboard (no rating).

    Returns benchmark_results dict with models sorted by win rate.
    """
    scored_games = [g for g in games_index if g.get("winner")]

    stats: dict[str, dict[str, float]] = {}
    for game in scored_games:
        blunder_weight_by_name: dict[str, float] = {}
        annotations = game.get("annotations")
        if annotations is not None:
            for ann in annotations:
                ann_type, name, severity = ann.type, ann.player, ann.severity
                if ann_type == "blunder":
                    if not name:
                        continue
                    blunder_weight_by_name[name] = blunder_weight_by_name.get(name, 0) + BLUNDER_WEIGHTS.get(
                        severity, 0
                    )

        total_turns = game.get("totalTurns", 0)

        for p in game["players"]:
            if p.type != "pilot" or not p.model:
                continue
            key = _player_key(p.model, p.reasoningEffort)
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
            if game.get("winner") == p.name:
                stats[key]["wins"] += 1
            if p.timedOut:
                stats[key]["timeout_losses"] += 1
            stats[key]["total_cost"] += p.totalCostUsd or 0.0
            stats[key]["total_tool_calls_ok"] += p.toolCallsOk
            stats[key]["total_tool_calls_failed"] += p.toolCallsFailed
            stats[key]["total_thinking_time"] += p.thinkingTimeSecs
            assert game.get("annotations") is not None, f"Game {game.get('id')} has no annotations"
            assert total_turns > 0, f"Game {game.get('id')} has no turns"
            stats[key]["total_annotated_turns"] += total_turns
            stats[key]["total_weighted_blunders"] += blunder_weight_by_name.get(p.name, 0)

    models: list[_ModelEntry] = []
    for key, s in stats.items():
        model_id, effort = _split_key(key)
        games_played = int(s["games_played"])
        wins = int(s["wins"])
        win_rate = wins / games_played
        avg_cost = s["total_cost"] / games_played
        provider_slug = model_id.split("/", 1)[0]

        display_name = model_registry.get(model_id) or derive_display_name(model_id)
        if effort:
            display_name = f"{display_name} ({effort})"

        avg_tool_calls_ok = s["total_tool_calls_ok"] / games_played
        avg_tool_calls_failed = s["total_tool_calls_failed"] / games_played
        avg_thinking_time = s["total_thinking_time"] / games_played
        total_annotated_turns = int(s["total_annotated_turns"])
        assert total_annotated_turns > 0, f"Model {model_id} has no annotated turns"
        blunder_score = s["total_weighted_blunders"] / total_annotated_turns
        timeout_losses = int(s["timeout_losses"])
        timeout_loss_rate = timeout_losses / games_played
        models.append(
            _ModelEntry(
                model_id=model_id,
                model_name=display_name,
                provider=capitalize_provider(provider_slug),
                rating=None,
                games_played=games_played,
                win_rate=round(win_rate, 4),
                timeout_losses=timeout_losses,
                timeout_loss_rate=round(timeout_loss_rate, 4),
                avg_api_cost=round(avg_cost, 2),
                avg_tool_calls_ok=round(avg_tool_calls_ok, 1),
                avg_tool_calls_failed=round(avg_tool_calls_failed, 1),
                avg_thinking_time_secs=round(avg_thinking_time, 1),
                blunder_score=round(blunder_score, 2),
                reasoning_effort=effort,
            )
        )

    # Sort by win rate desc, then games played desc, then modelId for determinism
    models.sort(key=_exhibition_sort_key)

    return {
        "generatedAt": datetime.now(UTC).isoformat(),
        "totalGames": len(scored_games),
        "exhibition": True,
        "models": _serialize_model_entries(models),
    }


def generate_all_leaderboards(
    games_index: list[dict],
    model_registry: dict[str, str],
    games_dir: Path | None = None,
) -> tuple[dict[str, dict], dict[str, dict[str, dict[str, int]]]]:
    """Generate per-format leaderboards plus a combined view.

    Returns (format_results, ratings_by_game) where format_results maps
    each format to its benchmark_results dict. Rated pools use Elo.
    Exhibition pools (Commander) get stats only, no rating. The "combined"
    pool includes only rated (1v1) games.
    """
    # Partition games by format
    games_by_format: dict[str, list[dict]] = {fmt: [] for fmt in _FORMAT_POOLS}
    for g in games_index:
        fmt = derive_format(g)
        if fmt in games_by_format:
            games_by_format[fmt].append(g)

    format_results: dict[str, dict] = {}
    ratings_by_game: dict[str, dict[str, dict[str, int]]] = {}

    # Independent Elo pool per rated format
    for fmt in _RATED_POOLS:
        results, ratings = generate_leaderboard(games_by_format[fmt], model_registry, games_dir)
        format_results[fmt] = results
        ratings_by_game.update(ratings)

    # Exhibition pools: stats only, no rating
    for fmt in _EXHIBITION_POOLS:
        format_results[fmt] = generate_exhibition_leaderboard(games_by_format[fmt], model_registry)

    # Combined: only rated (1v1) games in one pool
    rated_games = [g for fmt in _RATED_POOLS for g in games_by_format[fmt]]
    combined_results, _ = generate_leaderboard(rated_games, model_registry, games_dir)
    format_results["combined"] = combined_results

    return format_results, ratings_by_game


def generate_leaderboard_file(
    games_dir: Path,
    data_dir: Path,
    models_json: Path,
    current_season: int | None = None,
) -> Path:
    """Generate leaderboard files from game data.

    Writes:
    - data_dir/benchmark-results.json (model summaries for leaderboard page)
    - games_dir/../data/elo.json (per-game ratings for game pages)

    Returns the benchmark-results.json path.
    """
    games_index = []
    for gz_path in _glob_game_files(games_dir):
        game = _load_game_file(gz_path)
        players = game.players

        game_entry: dict[str, Any] = {
            "id": game.id,
            "timestamp": game.timestamp,
            "gameType": game.gameType,
            "deckType": game.deckType,
            "totalTurns": game.totalTurns,
            "winner": game.winner,
            "players": players,
            "harnessEpoch": game.harnessEpoch,
            "season": game.season,
        }
        if game.annotations is not None:
            game_entry["annotations"] = game.annotations
        if game.tournament:
            game_entry["tournament"] = True
        games_index.append(game_entry)

    model_registry = load_model_registry(models_json)
    inactive_statuses = _load_inactive_statuses(models_json.parent / "presets.json")

    def _mark_inactive(fmt_results: dict[str, Any]) -> None:
        if inactive_statuses is None:
            return
        for fmt_data in fmt_results.values():
            for model in fmt_data["models"]:
                model_id = model["modelId"]
                effort = model.get("reasoningEffort")
                key = f"{model_id}::{effort}" if effort else model_id
                if key in inactive_statuses:
                    model["inactive"] = inactive_statuses[key]

    def _build_output(season_games: list[dict]) -> tuple[dict[str, Any], dict[str, Any]]:
        fmt_results, ratings = generate_all_leaderboards(season_games, model_registry, games_dir)
        _mark_inactive(fmt_results)
        pool = fmt_results.get("combined", {"generatedAt": "", "totalGames": 0, "models": []})
        total = sum(fmt_results[fmt].get("totalGames", 0) for fmt in _FORMAT_POOLS if fmt in fmt_results)
        return {
            "generatedAt": pool.get("generatedAt") if "generatedAt" in pool else "",
            "totalGames": total,
            "models": pool["models"],
            "formats": fmt_results,
            "minBlunderVersion": MIN_BLUNDER_VERSION,
        }, ratings

    # Group games by season and generate per-season leaderboard files
    games_by_season: dict[int, list[dict]] = {}
    for g in games_index:
        games_by_season.setdefault(g["season"], []).append(g)

    data_dir.mkdir(parents=True, exist_ok=True)
    all_ratings: dict[str, Any] = {}
    available_seasons: list[int] = sorted(games_by_season.keys())
    if current_season is not None and current_season not in available_seasons:
        available_seasons.append(current_season)
        available_seasons.sort()

    # Per-season files go to public/data/ for client-side fetch
    public_data_dir = games_dir.parent / "data"
    public_data_dir.mkdir(parents=True, exist_ok=True)

    for season_num in available_seasons:
        season_games = games_by_season.get(season_num)
        season_output, season_ratings = _build_output(season_games if season_games is not None else [])
        season_output["availableSeasons"] = available_seasons
        season_path = public_data_dir / f"benchmark-results-season-{season_num}.json"
        _write_if_changed(season_path, json.dumps(season_output, indent=2) + "\n")
        all_ratings.update(season_ratings)

    # Primary benchmark-results.json = all rated games (season >= 1)
    rated_seasons = [s for s in available_seasons if s >= 1]
    if len(rated_seasons) == 1 and rated_seasons[0] in games_by_season:
        # Reuse already-computed result for the single rated season
        output, ratings_by_game = _build_output(games_by_season[rated_seasons[0]])
    else:
        rated_games = [g for g in games_index if g["season"] >= 1]
        output, ratings_by_game = _build_output(rated_games)
    all_ratings.update(ratings_by_game)
    output["availableSeasons"] = available_seasons
    output_path = data_dir / "benchmark-results.json"
    _write_if_changed(output_path, json.dumps(output, indent=2) + "\n")

    # Write ratings.json to public/data/
    ratings_path = public_data_dir / "ratings.json"
    _write_if_changed(ratings_path, json.dumps(all_ratings, indent=2) + "\n")

    return output_path


def generate_model_stats(games_dir: Path, data_dir: Path, models_json: Path) -> Path:
    """Generate per-model operational stats from game data.

    Aggregates timeout rates, error breakdowns, token usage, context resets,
    and other diagnostics bucketed by (model, epoch) for client-side filtering.

    Includes ALL games (with or without winner) since operational stats matter
    even for crashed games.

    Writes data_dir/model-stats.json and returns its path.
    """
    model_registry = load_model_registry(models_json)

    # Keyed by (player_key, epoch) -> stats bucket
    buckets: dict[tuple[str, int], dict[str, Any]] = {}
    # Collect per-request latencies for percentile computation
    latencies: dict[tuple[str, int], list[float]] = {}
    # Track model metadata
    model_meta: dict[str, dict[str, str]] = {}

    for gz_path in _glob_game_files(games_dir):
        game = _load_game_file(gz_path)
        epoch = game.harnessEpoch
        players = game.players
        winner = game.winner

        # Build name -> player_key map for this game
        name_to_key: dict[str, str] = {}
        for p in players:
            if not is_pilot_player(p):
                continue
            key = _player_key(p.model, p.reasoningEffort)
            name_to_key[p.name] = key

            # Register model metadata (first time only)
            if key not in model_meta:
                model_id, effort = _split_key(key)
                display_name = model_registry.get(model_id) or derive_display_name(model_id)
                if effort:
                    display_name = f"{display_name} ({effort})"
                provider_slug = model_id.split("/", 1)[0]
                meta: dict[str, str] = {
                    "modelId": model_id,
                    "modelName": display_name,
                    "provider": capitalize_provider(provider_slug),
                }
                if effort:
                    meta["reasoningEffort"] = effort
                model_meta[key] = meta

            # Initialize bucket
            bucket_key = (key, epoch)
            if bucket_key not in buckets:
                buckets[bucket_key] = {
                    "gamesPlayed": 0,
                    "wins": 0,
                    "timerTimeoutLosses": 0,
                    "totalCostUsd": 0.0,
                    "totalToolCallsOk": 0,
                    "totalToolCallsFailed": 0,
                    "totalThinkingTimeSecs": 0.0,
                    "totalPromptTokens": 0,
                    "totalCompletionTokens": 0,
                    "totalCachedTokens": 0,
                    "totalReasoningTokens": 0,
                    "successfulResponses": 0,
                    "errors": {},
                    "contextResets": 0,
                }

            b = buckets[bucket_key]
            b["gamesPlayed"] += 1
            if winner == p.name:
                b["wins"] += 1
            if p.timedOut:
                b["timerTimeoutLosses"] += 1
            b["totalCostUsd"] += p.totalCostUsd or 0.0
            b["totalToolCallsOk"] += p.toolCallsOk
            b["totalToolCallsFailed"] += p.toolCallsFailed
            b["totalThinkingTimeSecs"] += p.thinkingTimeSecs

        # Scan llmEvents for per-player operational stats
        llm_events = game.llmEvents
        for ev in llm_events:
            player_name = ev.player
            if player_name not in name_to_key:
                continue
            key = name_to_key[player_name]
            bucket_key = (key, epoch)
            if bucket_key not in buckets:
                continue
            b = buckets[bucket_key]

            if isinstance(ev, LlmResponseEvent):
                b["successfulResponses"] += 1
                usage = ev.usage
                if usage is not None:
                    b["totalPromptTokens"] += usage.promptTokens or 0
                    b["totalCompletionTokens"] += usage.completionTokens or 0
                    b["totalCachedTokens"] += usage.cachedTokens or 0
                    b["totalReasoningTokens"] += usage.reasoningTokens or 0
            elif isinstance(ev, LlmErrorEvent):
                error_type = ev.errorType or "unknown"
                b["errors"][error_type] = b["errors"].get(error_type, 0) + 1
            elif ev.type == "context_reset":
                b["contextResets"] += 1

        # Collect per-request latencies from consecutive event timestamps.
        # Each consecutive pair (ev_i, ev_{i+1}) attributes the time gap to
        # ev_i's player — same approach as compute_thinking_time but we
        # collect individual durations instead of summing.
        for i in range(len(llm_events) - 1):
            player_name = llm_events[i].player
            if player_name not in name_to_key:
                continue
            ts_a = llm_events[i].ts
            ts_b = llm_events[i + 1].ts
            if not ts_a or not ts_b:
                continue
            try:
                dt_a = datetime.fromisoformat(ts_a)
                dt_b = datetime.fromisoformat(ts_b)
            except ValueError:
                continue
            gap = (dt_b - dt_a).total_seconds()
            if gap > 0:
                key = name_to_key[player_name]
                bucket_key = (key, epoch)
                if bucket_key not in latencies:
                    latencies[bucket_key] = []
                latencies[bucket_key].append(gap)

    # Compute latency percentiles and attach to buckets
    for bucket_key, durations in latencies.items():
        if bucket_key not in buckets:
            continue
        b = buckets[bucket_key]
        durations.sort()
        n = len(durations)
        b["latencyP50"] = round(durations[n // 2], 1) if n > 0 else 0.0
        p95_idx = min(math.ceil(n * 0.95) - 1, n - 1)
        b["latencyP95"] = round(durations[p95_idx], 1) if n > 0 else 0.0
        b["latencySamples"] = n

    # Ensure buckets without latency data still have the fields
    for bucket in buckets.values():
        bucket.setdefault("latencyP50", 0.0)
        bucket.setdefault("latencyP95", 0.0)
        bucket.setdefault("latencySamples", 0)

    # Assemble output grouped by model
    models_out: dict[str, Any] = {}
    for (key, epoch), bucket in buckets.items():
        if key not in models_out:
            models_out[key] = {**model_meta[key], "epochs": {}}
        # Round cost for JSON readability
        bucket["totalCostUsd"] = round(bucket["totalCostUsd"], 4)
        bucket["totalThinkingTimeSecs"] = round(bucket["totalThinkingTimeSecs"], 1)
        models_out[key]["epochs"][str(epoch)] = bucket

    # Sort models by key for deterministic output
    sorted_models = dict(sorted(models_out.items()))

    output: dict[str, Any] = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "models": sorted_models,
    }

    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / "model-stats.json"
    _write_if_changed(output_path, json.dumps(output, indent=2) + "\n")
    return output_path


def generate_internals_data(games_dir: Path, data_dir: Path, models_json: Path) -> Path:
    """Generate per-game per-player data points for internals trend charts.

    Produces a flat list of records — one per player per game — with all
    operational metrics needed for client-side aggregation and charting.

    Includes ALL games (with or without winner) since operational stats
    matter even for crashed games.

    Writes data_dir/internals-data.json and returns its path.
    """
    model_registry = load_model_registry(models_json)

    games_out: list[dict[str, Any]] = []

    for gz_path in _glob_game_files(games_dir):
        game = _load_game_file(gz_path)
        epoch = game.harnessEpoch
        game_format = derive_format(game)
        winner = game.winner
        players = game.players

        # Build name -> player_key map for this game
        name_to_key: dict[str, str] = {}
        for p in players:
            if not is_pilot_player(p):
                continue
            name_to_key[p.name] = _player_key(p.model, p.reasoningEffort)

        # Accumulate per-player stats from llmEvents
        player_responses: dict[str, int] = {}
        player_timeouts: dict[str, int] = {}
        player_other_errors: dict[str, int] = {}
        player_context_resets: dict[str, int] = {}
        player_prompt_tokens: dict[str, int] = {}
        player_completion_tokens: dict[str, int] = {}
        player_cached_tokens: dict[str, int] = {}
        player_reasoning_tokens: dict[str, int] = {}
        player_latencies: dict[str, list[float]] = {}

        # Track last event timestamp per player for latency gaps
        last_ts: dict[str, datetime] = {}

        for ev in game.llmEvents:
            player_name = ev.player
            if player_name not in name_to_key:
                continue

            if isinstance(ev, LlmResponseEvent):
                player_responses[player_name] = player_responses.get(player_name, 0) + 1
                usage = ev.usage
                if usage is not None:
                    player_prompt_tokens[player_name] = player_prompt_tokens.get(player_name, 0) + (
                        usage.promptTokens or 0
                    )
                    player_completion_tokens[player_name] = player_completion_tokens.get(player_name, 0) + (
                        usage.completionTokens or 0
                    )
                    player_cached_tokens[player_name] = player_cached_tokens.get(player_name, 0) + (
                        usage.cachedTokens or 0
                    )
                    player_reasoning_tokens[player_name] = player_reasoning_tokens.get(player_name, 0) + (
                        usage.reasoningTokens or 0
                    )
            elif isinstance(ev, LlmErrorEvent):
                error_type = ev.errorType or "unknown"
                if error_type == "timeout":
                    player_timeouts[player_name] = player_timeouts.get(player_name, 0) + 1
                else:
                    player_other_errors[player_name] = player_other_errors.get(player_name, 0) + 1
            elif ev.type == "context_reset":
                player_context_resets[player_name] = player_context_resets.get(player_name, 0) + 1

            # Track latency from inter-event timestamp gaps
            ev_ts_str = ev.ts
            if ev_ts_str:
                try:
                    ev_ts = datetime.fromisoformat(ev_ts_str)
                except ValueError:
                    continue
                if player_name in last_ts:
                    gap = (ev_ts - last_ts[player_name]).total_seconds()
                    if gap > 0:
                        player_latencies.setdefault(player_name, []).append(gap)
                last_ts[player_name] = ev_ts

        # Parse the game timestamp into ISO format for date-based charting
        raw_ts = game.timestamp
        iso_ts = ""
        if raw_ts:
            # Timestamps are like "20260210_074307"
            try:
                dt = datetime.strptime(raw_ts, "%Y%m%d_%H%M%S").replace(tzinfo=_GAME_TIMESTAMP_TZ)
                iso_ts = dt.strftime("%Y-%m-%dT%H:%M:%S")
            except ValueError:
                iso_ts = raw_ts

        # Build per-player records
        player_records: list[dict[str, Any]] = []
        for p in players:
            if not is_pilot_player(p):
                continue
            key = _player_key(p.model, p.reasoningEffort)
            model_id, effort = _split_key(key)
            display_name = model_registry.get(model_id) or derive_display_name(model_id)
            if effort:
                display_name = f"{display_name} ({effort})"
            name = p.name

            durations = player_latencies.get(name)
            if durations is not None:
                durations.sort()
                lat_p50 = round(durations[len(durations) // 2], 1)
            else:
                lat_p50 = None

            player_records.append(
                {
                    "key": key,
                    "modelName": display_name,
                    "won": winner == name,
                    "timedOut": bool(p.timedOut),
                    "costUsd": round(p.totalCostUsd or 0.0, 4),
                    "promptTokens": player_prompt_tokens.get(name, 0),
                    "completionTokens": player_completion_tokens.get(name, 0),
                    "cachedTokens": player_cached_tokens.get(name, 0),
                    "reasoningTokens": player_reasoning_tokens.get(name, 0),
                    "toolCallsOk": p.toolCallsOk,
                    "toolCallsFailed": p.toolCallsFailed,
                    "thinkingTimeSecs": round(p.thinkingTimeSecs, 1),
                    "responses": player_responses.get(name, 0),
                    "timeouts": player_timeouts.get(name, 0),
                    "otherErrors": player_other_errors.get(name, 0),
                    "contextResets": player_context_resets.get(name, 0),
                    "latencyP50": lat_p50,
                }
            )

        games_out.append(
            {
                "id": game.id,
                "ts": iso_ts,
                "epoch": epoch,
                "format": game_format,
                "players": player_records,
            }
        )

    # Sort by timestamp for consistent ordering
    games_out.sort(key=lambda g: g["ts"])

    output: dict[str, Any] = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "games": games_out,
    }

    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / "internals-data.json"
    _write_if_changed(output_path, json.dumps(output, indent=2) + "\n")
    return output_path


def generate_blunder_stats(data_dir: Path) -> Path:
    """Read blunder-stats.jsonl and write blunder-internals.json.

    The JSONL file is appended to by blunder_analysis.py on each annotation
    run.  This function parses it into a JSON array sorted by timestamp for
    the internals page.
    """
    jsonl_path = data_dir / "blunder-stats.jsonl"
    records: list[dict[str, Any]] = []
    if jsonl_path.exists():
        for line in jsonl_path.read_text().splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))

    records.sort(key=lambda r: r["ts"] if "ts" in r else "")

    output: dict[str, Any] = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "runs": records,
    }

    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / "blunder-internals.json"
    _write_if_changed(output_path, json.dumps(output, indent=2) + "\n")
    return output_path
