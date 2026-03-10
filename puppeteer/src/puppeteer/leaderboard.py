"""Generate leaderboard data from game results using Elo ratings."""

from __future__ import annotations

import gzip
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from typing_extensions import NotRequired

from puppeteer.harness_epoch import MIN_BLUNDER_VERSION

_GENERATED_AT_RE = re.compile(r'"generatedAt":\s*"[^"]*",?\n?')


class _ModelEntry(TypedDict):
    modelId: str
    modelName: str
    provider: str
    rating: int | None
    gamesPlayed: int
    winRate: float
    timeoutLosses: int
    timeoutLossRate: float
    avgApiCost: float
    avgToolCallsOk: float
    avgToolCallsFailed: float
    avgThinkingTimeSecs: float
    blunderScore: float
    reasoningEffort: NotRequired[str]


_LOST_GAME_RE = re.compile(r"^(.+?) has lost the game\.$")


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


def _load_game_file(path: Path) -> dict:
    """Load a game export file (.json or .json.gz)."""
    if path.suffix == ".gz":
        return json.loads(gzip.decompress(path.read_bytes()))
    return json.loads(path.read_text())


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
        player = llm_events[i].get("player", "")
        if not player:
            continue
        ts_a = llm_events[i].get("ts", "")
        ts_b = llm_events[i + 1].get("ts", "")
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


def derive_format(game: dict) -> str:
    """Derive canonical format name from game data.

    Uses deckType if present, falls back to 'commander' for
    backward compatibility with existing games.
    """
    deck_type = game.get("deckType", "")
    if deck_type in _DECK_TYPE_TO_FORMAT:
        return _DECK_TYPE_TO_FORMAT[deck_type]
    # Backward compat: old games without deckType were all Commander
    game_type = game.get("gameType", "")
    if "Commander" in game_type or not deck_type:
        return "commander"
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


def _player_key(player: dict) -> str:
    """Build aggregation key: 'model_id::effort' or just 'model_id'."""
    model_id = player.get("model", "")
    effort = player.get("reasoningEffort") or player.get("reasoning_effort")
    if effort:
        return f"{model_id}::{effort}"
    return model_id


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
    return {m["id"]: m["name"] for m in data.get("models", [])}


def _load_inactive_statuses(presets_json: Path) -> dict[str, str] | None:
    """Load inactive statuses for non-active presets from presets.json.

    Returns a dict mapping player_key -> status (e.g. "retired", "buggy", "expensive")
    for presets whose status is not "active".
    Returns None if presets.json doesn't exist (e.g. in tests).
    """
    if not presets_json.exists():
        return None
    data = json.loads(presets_json.read_text())
    presets = data.get("presets", {})
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


def extract_placements(game: dict, games_dir: Path | None = None) -> dict[str, int]:
    """Extract player placements from game data.

    Uses the 'placement' field if present on players. Otherwise, falls back
    to parsing "X has lost the game." messages from the full game JSON file
    (if games_dir is provided).

    Returns {player_name: placement} where 1=winner, 2=2nd, etc.
    """
    players = game.get("players", [])

    # Check if placements are already in the index data
    if any("placement" in p for p in players):
        return {p["name"]: p["placement"] for p in players if "placement" in p}

    # Fall back to reading actions from the full game JSON
    if games_dir is None:
        return _placements_from_winner(game)

    game_path = games_dir / f"{game['id']}.json.gz"
    if not game_path.exists():
        game_path = games_dir / f"{game['id']}.json"
    if not game_path.exists():
        return _placements_from_winner(game)

    full_game = _load_game_file(game_path)
    actions = full_game.get("actions", [])
    player_names = [p.get("name", "?") for p in players]
    winner = game.get("winner")

    eliminations = []
    for a in actions:
        m = _LOST_GAME_RE.match(a.get("message", ""))
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


def _placements_from_winner(game: dict) -> dict[str, int]:
    """Minimal placement extraction using only winner field."""
    winner = game.get("winner")
    if not winner:
        return {}
    placements: dict[str, int] = {}
    for p in game.get("players", []):
        name = p.get("name", "?")
        placements[name] = 1 if name == winner else 2
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

    sorted_games = sorted(games_index, key=lambda g: g.get("timestamp", ""))

    for game in sorted_games:
        pilots = [p for p in game.get("players", []) if p.get("type") == "pilot" and p.get("model")]
        if len(pilots) < 2:
            for p in pilots:
                key = _player_key(p)
                if key not in ratings:
                    ratings[key] = float(_ELO_START)
            if pilots:
                key = _player_key(pilots[0])
                per_game.append(
                    {
                        "id": game.get("id", ""),
                        "players": [
                            {"key": key, "ratingBefore": round(ratings[key]), "ratingAfter": round(ratings[key])}
                        ],
                    }
                )
            continue

        for p in pilots:
            key = _player_key(p)
            if key not in ratings:
                ratings[key] = float(_ELO_START)

        pilot_keys = [_player_key(p) for p in pilots]
        before = {key: round(ratings[key]) for key in pilot_keys}

        placements = extract_placements(game, games_dir)
        has_placements = any(p["name"] in placements for p in pilots)

        if has_placements and len(pilots) == 2:
            key_a, key_b = pilot_keys
            ra, rb = ratings[key_a], ratings[key_b]
            ea = 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))
            eb = 1.0 - ea

            # Determine scores: winner gets 1, loser gets 0
            placement_a = placements.get(pilots[0]["name"])
            sa = 1.0 if placement_a == 1 else 0.0
            sb = 1.0 - sa

            ratings[key_a] = ra + _ELO_K * (sa - ea)
            ratings[key_b] = rb + _ELO_K * (sb - eb)

        after = {key: round(ratings[key]) for key in pilot_keys}
        per_game.append(
            {
                "id": game.get("id", ""),
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
        for ann in game.get("annotations", []):
            if ann.get("type") == "blunder":
                name = ann.get("player", "")
                severity = ann.get("severity", "")
                blunder_weight_by_name[name] = blunder_weight_by_name.get(name, 0) + BLUNDER_WEIGHTS.get(severity, 0)

        total_turns = game.get("totalTurns", 0)

        for p in game.get("players", []):
            if p.get("type") != "pilot" or not p.get("model"):
                continue
            key = _player_key(p)
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
            if game.get("winner") == p["name"]:
                stats[key]["wins"] += 1
            if p.get("timedOut"):
                stats[key]["timeout_losses"] += 1
            stats[key]["total_cost"] += p.get("totalCostUsd", 0.0)
            stats[key]["total_tool_calls_ok"] += p.get("toolCallsOk", 0)
            stats[key]["total_tool_calls_failed"] += p.get("toolCallsFailed", 0)
            stats[key]["total_thinking_time"] += p.get("thinkingTimeSecs", 0.0)
            assert game.get("annotations") is not None, f"Game {game.get('id')} has no annotations"
            assert total_turns > 0, f"Game {game.get('id')} has no turns"
            stats[key]["total_annotated_turns"] += total_turns
            stats[key]["total_weighted_blunders"] += blunder_weight_by_name.get(p["name"], 0)

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
        entry: _ModelEntry = {
            "modelId": model_id,
            "modelName": display_name,
            "provider": capitalize_provider(provider_slug),
            "rating": rating,
            "gamesPlayed": games_played,
            "winRate": round(win_rate, 4),
            "timeoutLosses": timeout_losses,
            "timeoutLossRate": round(timeout_loss_rate, 4),
            "avgApiCost": round(avg_cost, 2),
            "avgToolCallsOk": round(avg_tool_calls_ok, 1),
            "avgToolCallsFailed": round(avg_tool_calls_failed, 1),
            "avgThinkingTimeSecs": round(avg_thinking_time, 1),
            "blunderScore": round(blunder_score, 2),
        }
        if effort:
            entry["reasoningEffort"] = effort
        models.append(entry)

    # Sort by rating desc, then games_played desc, then modelId for determinism
    def rated_sort_key(m: _ModelEntry) -> tuple[int, int, str, str]:
        assert m["rating"] is not None
        return (-m["rating"], -m["gamesPlayed"], m["modelId"], m.get("reasoningEffort", ""))

    models.sort(key=rated_sort_key)

    benchmark_results = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "totalGames": len(scored_games),
        "models": models,
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
        for ann in game.get("annotations", []):
            if ann.get("type") == "blunder":
                name = ann.get("player", "")
                severity = ann.get("severity", "")
                blunder_weight_by_name[name] = blunder_weight_by_name.get(name, 0) + BLUNDER_WEIGHTS.get(severity, 0)

        total_turns = game.get("totalTurns", 0)

        for p in game.get("players", []):
            if p.get("type") != "pilot" or not p.get("model"):
                continue
            key = _player_key(p)
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
            if game.get("winner") == p["name"]:
                stats[key]["wins"] += 1
            if p.get("timedOut"):
                stats[key]["timeout_losses"] += 1
            stats[key]["total_cost"] += p.get("totalCostUsd", 0.0)
            stats[key]["total_tool_calls_ok"] += p.get("toolCallsOk", 0)
            stats[key]["total_tool_calls_failed"] += p.get("toolCallsFailed", 0)
            stats[key]["total_thinking_time"] += p.get("thinkingTimeSecs", 0.0)
            assert game.get("annotations") is not None, f"Game {game.get('id')} has no annotations"
            assert total_turns > 0, f"Game {game.get('id')} has no turns"
            stats[key]["total_annotated_turns"] += total_turns
            stats[key]["total_weighted_blunders"] += blunder_weight_by_name.get(p["name"], 0)

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
        entry: _ModelEntry = {
            "modelId": model_id,
            "modelName": display_name,
            "provider": capitalize_provider(provider_slug),
            "rating": None,
            "gamesPlayed": games_played,
            "winRate": round(win_rate, 4),
            "timeoutLosses": timeout_losses,
            "timeoutLossRate": round(timeout_loss_rate, 4),
            "avgApiCost": round(avg_cost, 2),
            "avgToolCallsOk": round(avg_tool_calls_ok, 1),
            "avgToolCallsFailed": round(avg_tool_calls_failed, 1),
            "avgThinkingTimeSecs": round(avg_thinking_time, 1),
            "blunderScore": round(blunder_score, 2),
        }
        if effort:
            entry["reasoningEffort"] = effort
        models.append(entry)

    # Sort by win rate desc, then games played desc, then modelId for determinism
    models.sort(key=lambda m: (-m["winRate"], -m["gamesPlayed"], m["modelId"], m.get("reasoningEffort", "")))

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "totalGames": len(scored_games),
        "exhibition": True,
        "models": models,
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


def _backfill_player_stats(game: dict) -> None:
    """Backfill toolCallsOk/toolCallsFailed/thinkingTimeSecs on players from llmEvents.

    Mutates player dicts in-place. Skips fields that are already present.
    """
    players = game.get("players", [])
    if not players:
        return

    # Compute tool call counts from llmEvents if not already on players
    if not any("toolCallsOk" in p for p in players):
        tool_ok: dict[str, int] = {}
        tool_failed: dict[str, int] = {}
        for ev in game.get("llmEvents", []):
            if ev.get("type") != "tool_call":
                continue
            player = ev.get("player", "")
            if not player:
                continue
            result_str = ev.get("result", "")
            is_failure = False
            if result_str:
                try:
                    result_obj = json.loads(result_str)
                    if isinstance(result_obj, dict) and result_obj.get("success") is False:
                        is_failure = True
                except (json.JSONDecodeError, TypeError):
                    pass
            if is_failure:
                tool_failed[player] = tool_failed.get(player, 0) + 1
            else:
                tool_ok[player] = tool_ok.get(player, 0) + 1
        for p in players:
            name = p.get("name", "")
            if name in tool_ok or name in tool_failed:
                p["toolCallsOk"] = tool_ok.get(name, 0)
                p["toolCallsFailed"] = tool_failed.get(name, 0)

    # Compute thinking time from llmEvents if not already on players
    if not any("thinkingTimeSecs" in p for p in players):
        llm_events = game.get("llmEvents", [])
        if llm_events:
            thinking = compute_thinking_time(llm_events)
            for p in players:
                name = p.get("name", "")
                if name in thinking:
                    p["thinkingTimeSecs"] = round(thinking[name], 1)


def generate_leaderboard_file(games_dir: Path, data_dir: Path, models_json: Path) -> Path:
    """Generate leaderboard files from game data.

    Writes:
    - data_dir/benchmark-results.json (model summaries for leaderboard page)
    - games_dir/../data/elo.json (per-game ratings for game pages)

    Returns the benchmark-results.json path.
    """
    games_index = []
    for gz_path in _glob_game_files(games_dir):
        game = _load_game_file(gz_path)
        players = game.get("players", [])

        _backfill_player_stats(game)

        game_entry: dict[str, Any] = {
            "id": game["id"],
            "timestamp": game.get("timestamp", ""),
            "gameType": game.get("gameType", ""),
            "deckType": game.get("deckType", ""),
            "totalTurns": game.get("totalTurns", 0),
            "winner": game.get("winner"),
            "players": players,
            "harnessEpoch": game.get("harnessEpoch"),
            "season": game["season"],
        }
        if "annotations" in game:
            game_entry["annotations"] = game["annotations"]
        if game.get("tournament"):
            game_entry["tournament"] = True
        games_index.append(game_entry)

    model_registry = load_model_registry(models_json)
    inactive_statuses = _load_inactive_statuses(models_json.parent / "presets.json")

    def _mark_inactive(fmt_results: dict[str, Any]) -> None:
        if inactive_statuses is None:
            return
        for fmt_data in fmt_results.values():
            for model in fmt_data.get("models", []):
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
            "generatedAt": pool.get("generatedAt", ""),
            "totalGames": total,
            "models": pool.get("models", []),
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

    # Per-season files go to public/data/ for client-side fetch
    public_data_dir = games_dir.parent / "data"
    public_data_dir.mkdir(parents=True, exist_ok=True)

    for season_num in available_seasons:
        season_output, season_ratings = _build_output(games_by_season[season_num])
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
        epoch = game.get("harnessEpoch", 0)
        players = game.get("players", [])
        winner = game.get("winner")

        _backfill_player_stats(game)

        # Build name -> player_key map for this game
        name_to_key: dict[str, str] = {}
        for p in players:
            if p.get("type") != "pilot" or not p.get("model"):
                continue
            key = _player_key(p)
            name_to_key[p["name"]] = key

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
            if winner == p["name"]:
                b["wins"] += 1
            if p.get("timedOut"):
                b["timerTimeoutLosses"] += 1
            b["totalCostUsd"] += p.get("totalCostUsd", 0.0)
            b["totalToolCallsOk"] += p.get("toolCallsOk", 0)
            b["totalToolCallsFailed"] += p.get("toolCallsFailed", 0)
            b["totalThinkingTimeSecs"] += p.get("thinkingTimeSecs", 0.0)

        # Scan llmEvents for per-player operational stats
        llm_events = game.get("llmEvents", [])
        for ev in llm_events:
            player_name = ev.get("player", "")
            if player_name not in name_to_key:
                continue
            key = name_to_key[player_name]
            bucket_key = (key, epoch)
            if bucket_key not in buckets:
                continue
            b = buckets[bucket_key]

            ev_type = ev.get("type")
            if ev_type == "llm_response":
                b["successfulResponses"] += 1
                usage = ev.get("usage", {})
                b["totalPromptTokens"] += usage.get("promptTokens", 0)
                b["totalCompletionTokens"] += usage.get("completionTokens", 0)
                b["totalCachedTokens"] += usage.get("cachedTokens", 0)
                b["totalReasoningTokens"] += usage.get("reasoningTokens", 0)
            elif ev_type == "llm_error":
                error_type = ev.get("errorType", "unknown")
                b["errors"][error_type] = b["errors"].get(error_type, 0) + 1
            elif ev_type == "context_reset":
                b["contextResets"] += 1

        # Collect per-request latencies from consecutive event timestamps.
        # Each consecutive pair (ev_i, ev_{i+1}) attributes the time gap to
        # ev_i's player — same approach as compute_thinking_time but we
        # collect individual durations instead of summing.
        for i in range(len(llm_events) - 1):
            player_name = llm_events[i].get("player", "")
            if player_name not in name_to_key:
                continue
            ts_a = llm_events[i].get("ts", "")
            ts_b = llm_events[i + 1].get("ts", "")
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
        "generatedAt": datetime.now(timezone.utc).isoformat(),
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
        epoch = game.get("harnessEpoch", 0)
        game_format = derive_format(game)
        winner = game.get("winner")
        players = game.get("players", [])

        _backfill_player_stats(game)

        # Build name -> player_key map for this game
        name_to_key: dict[str, str] = {}
        for p in players:
            if p.get("type") != "pilot" or not p.get("model"):
                continue
            name_to_key[p["name"]] = _player_key(p)

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

        for ev in game.get("llmEvents", []):
            player_name = ev.get("player", "")
            if player_name not in name_to_key:
                continue

            ev_type = ev.get("type")
            if ev_type == "llm_response":
                player_responses[player_name] = player_responses.get(player_name, 0) + 1
                usage = ev.get("usage", {})
                player_prompt_tokens[player_name] = player_prompt_tokens.get(player_name, 0) + usage.get(
                    "promptTokens", 0
                )
                player_completion_tokens[player_name] = player_completion_tokens.get(player_name, 0) + usage.get(
                    "completionTokens", 0
                )
                player_cached_tokens[player_name] = player_cached_tokens.get(player_name, 0) + usage.get(
                    "cachedTokens", 0
                )
                player_reasoning_tokens[player_name] = player_reasoning_tokens.get(player_name, 0) + usage.get(
                    "reasoningTokens", 0
                )
            elif ev_type == "llm_error":
                error_type = ev.get("errorType", "unknown")
                if error_type == "timeout":
                    player_timeouts[player_name] = player_timeouts.get(player_name, 0) + 1
                else:
                    player_other_errors[player_name] = player_other_errors.get(player_name, 0) + 1
            elif ev_type == "context_reset":
                player_context_resets[player_name] = player_context_resets.get(player_name, 0) + 1

            # Track latency from inter-event timestamp gaps
            ev_ts_str = ev.get("ts", "")
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
        raw_ts = game.get("timestamp", "")
        iso_ts = ""
        if raw_ts:
            # Timestamps are like "20260210_074307"
            try:
                dt = datetime.strptime(raw_ts, "%Y%m%d_%H%M%S")
                iso_ts = dt.isoformat()
            except ValueError:
                iso_ts = raw_ts

        # Build per-player records
        player_records: list[dict[str, Any]] = []
        for p in players:
            if p.get("type") != "pilot" or not p.get("model"):
                continue
            key = _player_key(p)
            model_id, effort = _split_key(key)
            display_name = model_registry.get(model_id) or derive_display_name(model_id)
            if effort:
                display_name = f"{display_name} ({effort})"
            name = p["name"]

            durations = player_latencies.get(name, [])
            if durations:
                durations.sort()
                lat_p50 = round(durations[len(durations) // 2], 1)
            else:
                lat_p50 = None

            player_records.append(
                {
                    "key": key,
                    "modelName": display_name,
                    "won": winner == name,
                    "timedOut": bool(p.get("timedOut")),
                    "costUsd": round(p.get("totalCostUsd", 0.0), 4),
                    "promptTokens": player_prompt_tokens.get(name, 0),
                    "completionTokens": player_completion_tokens.get(name, 0),
                    "cachedTokens": player_cached_tokens.get(name, 0),
                    "reasoningTokens": player_reasoning_tokens.get(name, 0),
                    "toolCallsOk": p.get("toolCallsOk", 0),
                    "toolCallsFailed": p.get("toolCallsFailed", 0),
                    "thinkingTimeSecs": round(p.get("thinkingTimeSecs", 0.0), 1),
                    "responses": player_responses.get(name, 0),
                    "timeouts": player_timeouts.get(name, 0),
                    "otherErrors": player_other_errors.get(name, 0),
                    "contextResets": player_context_resets.get(name, 0),
                    "latencyP50": lat_p50,
                }
            )

        games_out.append(
            {
                "id": game["id"],
                "ts": iso_ts,
                "epoch": epoch,
                "format": game_format,
                "players": player_records,
            }
        )

    # Sort by timestamp for consistent ordering
    games_out.sort(key=lambda g: g["ts"])

    output: dict[str, Any] = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
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

    records.sort(key=lambda r: r.get("ts", ""))

    output: dict[str, Any] = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "runs": records,
    }

    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / "blunder-internals.json"
    _write_if_changed(output_path, json.dumps(output, indent=2) + "\n")
    return output_path
