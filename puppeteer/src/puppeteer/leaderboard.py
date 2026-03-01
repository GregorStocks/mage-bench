"""Generate leaderboard data from game results using Elo and OpenSkill ratings."""

from __future__ import annotations

import gzip
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openskill.models import PlackettLuce

from puppeteer.harness_epoch import MIN_BLUNDER_VERSION, MIN_LEADERBOARD_EPOCH

_LOST_GAME_RE = re.compile(r"^(.+?) has lost the game\.$")


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


# Scale raw OpenSkill ordinals (centered at 0) to a DCI-style rating.
_OPENSKILL_BASE = 1600
_OPENSKILL_SCALE = 100

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
    "commander": "Commander",
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


def _openskill_display_rating(ordinal: float) -> int:
    """Convert raw OpenSkill ordinal to display rating."""
    return round(ordinal * _OPENSKILL_SCALE + _OPENSKILL_BASE)


def compute_openskill_ratings(
    games_index: list[dict],
    games_dir: Path | None = None,
) -> tuple[dict[str, float], list[dict]]:
    """Compute OpenSkill PlackettLuce ratings from game history.

    Uses full placement orderings for multiplayer games (e.g. Commander).
    Games with no placement data are skipped (no rating update) but still
    record snapshots.

    Returns (final_ratings, per_game_ratings) with the same shape as
    compute_elo_ratings.
    """
    model = PlackettLuce()
    os_ratings: dict[str, Any] = {}
    per_game: list[dict] = []

    sorted_games = sorted(games_index, key=lambda g: g.get("timestamp", ""))

    for game in sorted_games:
        pilots = [p for p in game.get("players", []) if p.get("type") == "pilot" and p.get("model")]
        if len(pilots) < 2:
            for p in pilots:
                key = _player_key(p)
                if key not in os_ratings:
                    os_ratings[key] = model.rating(name=key)
            if pilots:
                key = _player_key(pilots[0])
                display = _openskill_display_rating(os_ratings[key].ordinal())
                per_game.append(
                    {
                        "id": game.get("id", ""),
                        "players": [{"key": key, "ratingBefore": display, "ratingAfter": display}],
                    }
                )
            continue

        for p in pilots:
            key = _player_key(p)
            if key not in os_ratings:
                os_ratings[key] = model.rating(name=key)

        pilot_keys = [_player_key(p) for p in pilots]
        before = {key: _openskill_display_rating(os_ratings[key].ordinal()) for key in pilot_keys}

        placements = extract_placements(game, games_dir)

        teams = [[os_ratings[key]] for key in pilot_keys]
        has_placements = any(p["name"] in placements for p in pilots)
        if has_placements:
            # Winner-takes-all: 1st place wins, everyone else ties as losers.
            # Commander is "one winner, three losers" — elimination order
            # among non-winners is not a meaningful signal.
            ranks: list[float] = []
            for p in pilots:
                placement = placements.get(p["name"])
                ranks.append(1.0 if placement == 1 else 2.0)
            updated = model.rate(teams, ranks=ranks)
        else:
            updated = teams

        for i, key in enumerate(pilot_keys):
            os_ratings[key] = updated[i][0]

        after = {key: _openskill_display_rating(os_ratings[key].ordinal()) for key in pilot_keys}
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

    final: dict[str, float] = {}
    for mid, r in os_ratings.items():
        final[mid] = _openskill_display_rating(r.ordinal())

    return final, per_game


def generate_leaderboard(
    games_index: list[dict],
    model_registry: dict[str, str],
    games_dir: Path | None = None,
) -> tuple[dict, dict[str, dict[str, dict[str, int]]]]:
    """Aggregate game results into leaderboard data.

    Uses OpenSkill PlackettLuce for ratings.

    Returns (benchmark_results, ratings_by_game) where ratings_by_game is
    {game_id: {model_id: {before, after}}}.
    """
    # Filter to games with a winner for leaderboard purposes
    scored_games = [g for g in games_index if g.get("winner")]

    final_ratings, per_game = compute_openskill_ratings(scored_games, games_dir)

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
            stats[key]["total_cost"] += p.get("totalCostUsd", 0.0)
            stats[key]["total_tool_calls_ok"] += p.get("toolCallsOk", 0)
            stats[key]["total_tool_calls_failed"] += p.get("toolCallsFailed", 0)
            stats[key]["total_thinking_time"] += p.get("thinkingTimeSecs", 0.0)
            assert game.get("annotations") is not None, f"Game {game.get('id')} has no annotations"
            assert total_turns > 0, f"Game {game.get('id')} has no turns"
            stats[key]["total_annotated_turns"] += total_turns
            stats[key]["total_weighted_blunders"] += blunder_weight_by_name.get(p["name"], 0)

    # Build models list
    models: list[dict[str, str | int | float | None]] = []
    for key, s in stats.items():
        model_id, effort = _split_key(key)
        games_played = int(s["games_played"])
        wins = int(s["wins"])
        win_rate = wins / games_played
        avg_cost = s["total_cost"] / games_played
        provider_slug = model_id.split("/", 1)[0]
        rating = final_ratings.get(key, _OPENSKILL_BASE)

        display_name = model_registry.get(model_id) or derive_display_name(model_id)
        if effort:
            display_name = f"{display_name} ({effort})"

        avg_tool_calls_ok = s["total_tool_calls_ok"] / games_played
        avg_tool_calls_failed = s["total_tool_calls_failed"] / games_played
        avg_thinking_time = s["total_thinking_time"] / games_played
        total_annotated_turns = int(s["total_annotated_turns"])
        assert total_annotated_turns > 0, f"Model {model_id} has no annotated turns"
        blunder_score = s["total_weighted_blunders"] / total_annotated_turns
        entry: dict[str, str | int | float | None] = {
            "modelId": model_id,
            "modelName": display_name,
            "provider": capitalize_provider(provider_slug),
            "rating": rating,
            "gamesPlayed": games_played,
            "winRate": round(win_rate, 4),
            "avgApiCost": round(avg_cost, 2),
            "avgToolCallsOk": round(avg_tool_calls_ok, 1),
            "avgToolCallsFailed": round(avg_tool_calls_failed, 1),
            "avgThinkingTimeSecs": round(avg_thinking_time, 1),
            "blunderScore": round(blunder_score, 2),
        }
        if effort:
            entry["reasoningEffort"] = effort
        models.append(entry)

    # Sort by rating desc, then games_played desc
    models.sort(key=lambda m: (-m["rating"], -m["gamesPlayed"]))  # type: ignore[operator]

    benchmark_results = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "totalGames": len(scored_games),
        "models": models,
    }

    return benchmark_results, ratings_by_game


_FORMAT_POOLS = ("jumpstart", "standard", "modern", "legacy", "commander")


def generate_all_leaderboards(
    games_index: list[dict],
    model_registry: dict[str, str],
    games_dir: Path | None = None,
) -> tuple[dict[str, dict], dict[str, dict[str, dict[str, int]]]]:
    """Generate per-format leaderboards plus a combined view.

    Returns (format_results, ratings_by_game) where format_results maps
    each format in FORMAT_LABELS to its benchmark_results dict. All formats
    use OpenSkill PlackettLuce. The "combined" pool includes all games.
    """
    # Partition games by format
    games_by_format: dict[str, list[dict]] = {fmt: [] for fmt in _FORMAT_POOLS}
    for g in games_index:
        fmt = derive_format(g)
        if fmt in games_by_format:
            games_by_format[fmt].append(g)

    format_results: dict[str, dict] = {}
    ratings_by_game: dict[str, dict[str, dict[str, int]]] = {}

    # Independent OpenSkill pool per format
    for fmt in _FORMAT_POOLS:
        results, ratings = generate_leaderboard(games_by_format[fmt], model_registry, games_dir)
        format_results[fmt] = results
        ratings_by_game.update(ratings)

    # Combined: all games in one pool (separate rating computation, not used for per-game display)
    all_games = [g for games in games_by_format.values() for g in games]
    combined_results, _ = generate_leaderboard(all_games, model_registry, games_dir)
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
        }
        if "annotations" in game:
            game_entry["annotations"] = game["annotations"]
        games_index.append(game_entry)

    # Count games per epoch (all games, before filtering)
    epoch_counts: dict[int, int] = {}
    for g in games_index:
        e = g["harnessEpoch"]
        epoch_counts[e] = epoch_counts.get(e, 0) + 1

    # Filter to current epoch for leaderboard ratings
    rated_games = [g for g in games_index if g["harnessEpoch"] >= MIN_LEADERBOARD_EPOCH]
    excluded_count = len(games_index) - len(rated_games)

    model_registry = load_model_registry(models_json)
    format_results, ratings_by_game = generate_all_leaderboards(rated_games, model_registry, games_dir)

    # Mark inactive models (not in the active pool)
    inactive_statuses = _load_inactive_statuses(models_json.parent / "presets.json")
    if inactive_statuses is not None:
        for fmt_data in format_results.values():
            for model in fmt_data.get("models", []):
                model_id = model["modelId"]
                effort = model.get("reasoningEffort")
                key = f"{model_id}::{effort}" if effort else model_id
                if key in inactive_statuses:
                    model["inactive"] = inactive_statuses[key]

    # Build output with backward-compatible top-level fields from jumpstart (primary format)
    pool_jumpstart = format_results.get("jumpstart", {"generatedAt": "", "totalGames": 0, "models": []})
    # Sum games across real pools (not combined, which double-counts)
    total_games = sum(format_results[fmt].get("totalGames", 0) for fmt in _FORMAT_POOLS if fmt in format_results)
    output = {
        "generatedAt": pool_jumpstart.get("generatedAt", ""),
        "totalGames": total_games,
        "models": pool_jumpstart.get("models", []),
        "formats": format_results,
        "minEpoch": MIN_LEADERBOARD_EPOCH,
        "minBlunderVersion": MIN_BLUNDER_VERSION,
        "excludedGames": excluded_count,
        "epochCounts": {str(e): c for e, c in sorted(epoch_counts.items())},
    }

    # Write benchmark-results.json
    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / "benchmark-results.json"
    output_path.write_text(json.dumps(output, indent=2) + "\n")

    # Write ratings.json to public/data/
    ratings_dir = games_dir.parent / "data"
    ratings_dir.mkdir(parents=True, exist_ok=True)
    ratings_path = ratings_dir / "ratings.json"
    ratings_path.write_text(json.dumps(ratings_by_game, indent=2) + "\n")

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

    output: dict[str, Any] = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "minLeaderboardEpoch": MIN_LEADERBOARD_EPOCH,
        "models": models_out,
    }

    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / "model-stats.json"
    output_path.write_text(json.dumps(output, indent=2) + "\n")
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

            player_records.append(
                {
                    "key": key,
                    "modelName": display_name,
                    "won": winner == name,
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
        "minLeaderboardEpoch": MIN_LEADERBOARD_EPOCH,
        "games": games_out,
    }

    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / "internals-data.json"
    output_path.write_text(json.dumps(output, indent=2) + "\n")
    return output_path
