"""Generate leaderboard data from game results using Elo and OpenSkill ratings."""

from __future__ import annotations

import gzip
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openskill.models import PlackettLuce

from puppeteer.harness_epoch import MIN_LEADERBOARD_EPOCH

_LOST_GAME_RE = re.compile(r"^(.+?) has lost the game\.$")


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


_STARTING_RATING = 1600
_K_FACTOR = 32

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
}

# Display labels for leaderboard tabs.
FORMAT_LABELS: dict[str, str] = {
    "1v1": "1v1",
    "commander": "Commander",
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


def _expected_score(ra: float, rb: float) -> float:
    """Elo expected score for player A against player B."""
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


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
        return _placements_from_winner(game)

    full_game = json.loads(gzip.decompress(game_path.read_bytes()))
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


def compute_elo_ratings(
    games_index: list[dict],
    games_dir: Path | None = None,
) -> tuple[dict[str, float], list[dict]]:
    """Compute Elo ratings from game history.

    Processes games chronologically, updating ratings for 1v1 results.
    Games with no placement data are skipped (no rating update) but still
    record snapshots.

    Returns (final_ratings, per_game_ratings) where per_game_ratings is a list
    of dicts with {id, players: [{key, ratingBefore, ratingAfter}]}.
    """
    ratings: dict[str, float] = {}
    per_game: list[dict] = []

    sorted_games = sorted(games_index, key=lambda g: g.get("timestamp", ""))

    for game in sorted_games:
        pilots = [p for p in game.get("players", []) if p.get("type") == "pilot" and p.get("model")]
        if len(pilots) < 2:
            # Need at least 2 pilots for rating; record snapshot with no change
            for p in pilots:
                key = _player_key(p)
                if key not in ratings:
                    ratings[key] = float(_STARTING_RATING)
            if pilots:
                key = _player_key(pilots[0])
                display = round(ratings[key])
                per_game.append(
                    {
                        "id": game.get("id", ""),
                        "players": [{"key": key, "ratingBefore": display, "ratingAfter": display}],
                    }
                )
            continue

        # Ensure all pilots have a rating
        for p in pilots:
            key = _player_key(p)
            if key not in ratings:
                ratings[key] = float(_STARTING_RATING)

        # Record before ratings
        pilot_keys = [_player_key(p) for p in pilots]
        before = {key: round(ratings[key]) for key in pilot_keys}

        # Get placements
        placements = extract_placements(game, games_dir)

        has_placements = any(p["name"] in placements for p in pilots)
        if has_placements:
            # Pairwise Elo updates for all pilot pairs
            deltas: dict[str, float] = {key: 0.0 for key in pilot_keys}
            for i, pi in enumerate(pilots):
                for j, pj in enumerate(pilots):
                    if i >= j:
                        continue
                    ki = _player_key(pi)
                    kj = _player_key(pj)
                    pi_place = placements.get(pi["name"], len(pilots))
                    pj_place = placements.get(pj["name"], len(pilots))
                    if pi_place == pj_place:
                        continue
                    ea = _expected_score(ratings[ki], ratings[kj])
                    sa = 1.0 if pi_place < pj_place else 0.0
                    deltas[ki] += _K_FACTOR * (sa - ea)
                    deltas[kj] += _K_FACTOR * ((1.0 - sa) - (1.0 - ea))
            for key in pilot_keys:
                ratings[key] += deltas[key]

        # Record after ratings
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

    # Build final display ratings
    final: dict[str, float] = {}
    for mid, r in ratings.items():
        final[mid] = round(r)

    return final, per_game


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
            ranks: list[float] = []
            for p in pilots:
                placement = placements.get(p["name"])
                ranks.append(float(placement if placement is not None else len(pilots)))
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
    *,
    rating_fn: str = "elo",
) -> tuple[dict, dict[str, dict[str, dict[str, int]]]]:
    """Aggregate game results into leaderboard data.

    rating_fn selects the rating algorithm: "elo" or "openskill".

    Returns (benchmark_results, ratings_by_game) where ratings_by_game is
    {game_id: {model_id: {before, after}}}.
    """
    # Filter to games with a winner for leaderboard purposes
    scored_games = [g for g in games_index if g.get("winner")]

    compute_fn = compute_openskill_ratings if rating_fn == "openskill" else compute_elo_ratings
    final_ratings, per_game = compute_fn(scored_games, games_dir)

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
        # Build name -> blunder count from annotations.
        # "questionable" severity is excluded — it shows in the game viewer
        # but doesn't count toward leaderboard blunder stats.
        blunders_by_name: dict[str, int] = {}
        for ann in game.get("annotations", []):
            if ann.get("type") == "blunder" and ann.get("severity") != "questionable":
                name = ann.get("player", "")
                blunders_by_name[name] = blunders_by_name.get(name, 0) + 1

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
                    "total_blunders": 0,
                    "annotated_games": 0,
                }
            stats[key]["games_played"] += 1
            if game.get("winner") == p["name"]:
                stats[key]["wins"] += 1
            stats[key]["total_cost"] += p.get("totalCostUsd", 0.0)
            stats[key]["total_tool_calls_ok"] += p.get("toolCallsOk", 0)
            stats[key]["total_tool_calls_failed"] += p.get("toolCallsFailed", 0)
            stats[key]["total_thinking_time"] += p.get("thinkingTimeSecs", 0.0)
            if game.get("annotations") is not None:
                stats[key]["annotated_games"] += 1
                stats[key]["total_blunders"] += blunders_by_name.get(p["name"], 0)

    # Build models list
    models: list[dict[str, str | int | float | None]] = []
    for key, s in stats.items():
        model_id, effort = _split_key(key)
        games_played = int(s["games_played"])
        wins = int(s["wins"])
        win_rate = wins / games_played
        avg_cost = s["total_cost"] / games_played
        provider_slug = model_id.split("/", 1)[0]
        rating = final_ratings.get(key, _STARTING_RATING)

        display_name = model_registry.get(model_id) or derive_display_name(model_id)
        if effort:
            display_name = f"{display_name} ({effort})"

        avg_tool_calls_ok = s["total_tool_calls_ok"] / games_played
        avg_tool_calls_failed = s["total_tool_calls_failed"] / games_played
        avg_thinking_time = s["total_thinking_time"] / games_played
        annotated_games = int(s["annotated_games"])
        avg_blunders = s["total_blunders"] / annotated_games if annotated_games > 0 else None
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
            "avgBlundersPerGame": round(avg_blunders, 1) if avg_blunders is not None else None,
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


def generate_all_leaderboards(
    games_index: list[dict],
    model_registry: dict[str, str],
    games_dir: Path | None = None,
) -> tuple[dict[str, dict], dict[str, dict[str, dict[str, int]]]]:
    """Generate 1v1 and Commander leaderboards.

    Returns (format_results, ratings_by_game) where format_results maps
    "1v1" and "commander" to their respective benchmark_results dicts.
    1v1 uses Elo; Commander uses OpenSkill PlackettLuce.
    """
    # Partition into 1v1 (standard/modern/legacy) and commander
    games_1v1 = [g for g in games_index if derive_format(g) != "commander"]
    games_commander = [g for g in games_index if derive_format(g) == "commander"]

    # 1v1: all Standard/Modern/Legacy in one Elo pool
    results_1v1, ratings_1v1 = generate_leaderboard(games_1v1, model_registry, games_dir, rating_fn="elo")

    # Commander: OpenSkill PlackettLuce with full placement ordering
    results_commander, ratings_commander = generate_leaderboard(
        games_commander, model_registry, games_dir, rating_fn="openskill"
    )

    format_results: dict[str, dict] = {
        "1v1": results_1v1,
        "commander": results_commander,
    }

    # Merge per-game ratings (game IDs are unique across pools)
    ratings_by_game: dict[str, dict[str, dict[str, int]]] = {}
    ratings_by_game.update(ratings_1v1)
    ratings_by_game.update(ratings_commander)

    return format_results, ratings_by_game


def generate_leaderboard_file(games_dir: Path, data_dir: Path, models_json: Path) -> Path:
    """Generate leaderboard files from game data.

    Writes:
    - data_dir/benchmark-results.json (model summaries for leaderboard page)
    - games_dir/../data/elo.json (per-game ratings for game pages)

    Returns the benchmark-results.json path.
    """
    games_index = []
    for gz_path in sorted(games_dir.glob("game_*.json.gz")):
        game = json.loads(gzip.decompress(gz_path.read_bytes()))
        players = game.get("players", [])

        # Compute tool call counts from llmEvents if not already on players
        if players and not any("toolCallsOk" in p for p in players):
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
        if players and not any("thinkingTimeSecs" in p for p in players):
            llm_events = game.get("llmEvents", [])
            if llm_events:
                thinking = compute_thinking_time(llm_events)
                for p in players:
                    name = p.get("name", "")
                    if name in thinking:
                        p["thinkingTimeSecs"] = round(thinking[name], 1)

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

    # Build output with backward-compatible top-level fields from 1v1
    pool_1v1 = format_results.get("1v1", {"generatedAt": "", "totalGames": 0, "models": []})
    pool_cmdr = format_results.get("commander", {"totalGames": 0})
    total_games = pool_1v1.get("totalGames", 0) + pool_cmdr.get("totalGames", 0)
    output = {
        "generatedAt": pool_1v1.get("generatedAt", ""),
        "totalGames": total_games,
        "models": pool_1v1.get("models", []),
        "formats": format_results,
        "minEpoch": MIN_LEADERBOARD_EPOCH,
        "excludedGames": excluded_count,
        "epochCounts": {str(e): c for e, c in sorted(epoch_counts.items())},
    }

    # Write benchmark-results.json
    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / "benchmark-results.json"
    output_path.write_text(json.dumps(output, indent=2) + "\n")

    # Write elo.json to public/data/ (kept as elo.json for backward compat)
    elo_dir = games_dir.parent / "data"
    elo_dir.mkdir(parents=True, exist_ok=True)
    elo_path = elo_dir / "elo.json"
    elo_path.write_text(json.dumps(ratings_by_game, indent=2) + "\n")

    return output_path
