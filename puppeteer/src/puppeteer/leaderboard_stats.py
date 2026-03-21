"""Supporting stats generators for leaderboard data."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from puppeteer.leaderboard_common import glob_game_files, load_game_file, write_if_changed
from puppeteer.leaderboard_elo import _player_key, _split_key
from puppeteer.leaderboard_formats import derive_format
from puppeteer.leaderboard_registry import capitalize_provider, derive_display_name, load_model_registry
from schemas.game_export_types import LlmErrorEvent, LlmResponseEvent, is_pilot_player

_GAME_TIMESTAMP_TZ = ZoneInfo("America/Los_Angeles")


def _build_model_metadata(key: str, model_registry: dict[str, str]) -> dict[str, str]:
    model_id, effort = _split_key(key)
    display_name = model_registry.get(model_id) or derive_display_name(model_id)
    if effort:
        display_name = f"{display_name} ({effort})"
    provider_slug = model_id.split("/", 1)[0]
    metadata: dict[str, str] = {
        "modelId": model_id,
        "modelName": display_name,
        "provider": capitalize_provider(provider_slug),
    }
    if effort:
        metadata["reasoningEffort"] = effort
    return metadata


def _parse_game_timestamp(raw_ts: str | None) -> str:
    if not raw_ts:
        return ""
    try:
        dt = datetime.strptime(raw_ts, "%Y%m%d_%H%M%S").replace(tzinfo=_GAME_TIMESTAMP_TZ)
    except ValueError:
        return raw_ts
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def generate_model_stats(games_dir: Path, data_dir: Path, models_json: Path) -> Path:
    """Generate per-model operational stats from game data."""
    model_registry = load_model_registry(models_json)

    buckets: dict[tuple[str, int], dict[str, Any]] = {}
    latencies: dict[tuple[str, int], list[float]] = {}
    model_meta: dict[str, dict[str, str]] = {}

    for gz_path in glob_game_files(games_dir):
        game = load_game_file(gz_path)
        epoch = game.harnessEpoch
        winner = game.winner

        name_to_key: dict[str, str] = {}
        for player in game.players:
            if not is_pilot_player(player):
                continue
            key = _player_key(player.model, player.reasoningEffort)
            name_to_key[player.name] = key

            if key not in model_meta:
                model_meta[key] = _build_model_metadata(key, model_registry)

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

            bucket = buckets[bucket_key]
            bucket["gamesPlayed"] += 1
            if winner == player.name:
                bucket["wins"] += 1
            if player.timedOut:
                bucket["timerTimeoutLosses"] += 1
            bucket["totalCostUsd"] += player.totalCostUsd or 0.0
            bucket["totalToolCallsOk"] += player.toolCallsOk
            bucket["totalToolCallsFailed"] += player.toolCallsFailed
            bucket["totalThinkingTimeSecs"] += player.thinkingTimeSecs

        for event in game.llmEvents:
            player_name = event.player
            if player_name not in name_to_key:
                continue
            key = name_to_key[player_name]
            bucket_key = (key, epoch)
            if bucket_key not in buckets:
                continue
            bucket = buckets[bucket_key]

            if isinstance(event, LlmResponseEvent):
                bucket["successfulResponses"] += 1
                usage = event.usage
                if usage is not None:
                    bucket["totalPromptTokens"] += usage.promptTokens or 0
                    bucket["totalCompletionTokens"] += usage.completionTokens or 0
                    bucket["totalCachedTokens"] += usage.cachedTokens or 0
                    bucket["totalReasoningTokens"] += usage.reasoningTokens or 0
            elif isinstance(event, LlmErrorEvent):
                error_type = event.errorType or "unknown"
                bucket["errors"][error_type] = bucket["errors"].get(error_type, 0) + 1
            elif event.type == "context_reset":
                bucket["contextResets"] += 1

        for index in range(len(game.llmEvents) - 1):
            player_name = game.llmEvents[index].player
            if player_name not in name_to_key:
                continue
            ts_a = game.llmEvents[index].ts
            ts_b = game.llmEvents[index + 1].ts
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
                latencies.setdefault((key, epoch), []).append(gap)

    for bucket_key, durations in latencies.items():
        if bucket_key not in buckets:
            continue
        bucket = buckets[bucket_key]
        durations.sort()
        count = len(durations)
        bucket["latencyP50"] = round(durations[count // 2], 1) if count > 0 else 0.0
        p95_index = min(math.ceil(count * 0.95) - 1, count - 1)
        bucket["latencyP95"] = round(durations[p95_index], 1) if count > 0 else 0.0
        bucket["latencySamples"] = count

    for bucket in buckets.values():
        bucket.setdefault("latencyP50", 0.0)
        bucket.setdefault("latencyP95", 0.0)
        bucket.setdefault("latencySamples", 0)

    models_out: dict[str, Any] = {}
    for (key, epoch), bucket in buckets.items():
        if key not in models_out:
            models_out[key] = {**model_meta[key], "epochs": {}}
        bucket["totalCostUsd"] = round(bucket["totalCostUsd"], 4)
        bucket["totalThinkingTimeSecs"] = round(bucket["totalThinkingTimeSecs"], 1)
        models_out[key]["epochs"][str(epoch)] = bucket

    output: dict[str, Any] = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "models": dict(sorted(models_out.items())),
    }

    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / "model-stats.json"
    write_if_changed(output_path, json.dumps(output, indent=2) + "\n")
    return output_path


def generate_internals_data(games_dir: Path, data_dir: Path, models_json: Path) -> Path:
    """Generate per-game per-player data points for internals trend charts."""
    model_registry = load_model_registry(models_json)

    games_out: list[dict[str, Any]] = []

    for gz_path in glob_game_files(games_dir):
        game = load_game_file(gz_path)
        winner = game.winner

        name_to_key: dict[str, str] = {}
        for player in game.players:
            if not is_pilot_player(player):
                continue
            name_to_key[player.name] = _player_key(player.model, player.reasoningEffort)

        player_responses: dict[str, int] = {}
        player_timeouts: dict[str, int] = {}
        player_other_errors: dict[str, int] = {}
        player_context_resets: dict[str, int] = {}
        player_prompt_tokens: dict[str, int] = {}
        player_completion_tokens: dict[str, int] = {}
        player_cached_tokens: dict[str, int] = {}
        player_reasoning_tokens: dict[str, int] = {}
        player_latencies: dict[str, list[float]] = {}
        last_ts: dict[str, datetime] = {}

        for event in game.llmEvents:
            player_name = event.player
            if player_name not in name_to_key:
                continue

            if isinstance(event, LlmResponseEvent):
                player_responses[player_name] = player_responses.get(player_name, 0) + 1
                usage = event.usage
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
            elif isinstance(event, LlmErrorEvent):
                error_type = event.errorType or "unknown"
                if error_type == "timeout":
                    player_timeouts[player_name] = player_timeouts.get(player_name, 0) + 1
                else:
                    player_other_errors[player_name] = player_other_errors.get(player_name, 0) + 1
            elif event.type == "context_reset":
                player_context_resets[player_name] = player_context_resets.get(player_name, 0) + 1

            event_ts = event.ts
            if event_ts:
                try:
                    ts = datetime.fromisoformat(event_ts)
                except ValueError:
                    continue
                if player_name in last_ts:
                    gap = (ts - last_ts[player_name]).total_seconds()
                    if gap > 0:
                        player_latencies.setdefault(player_name, []).append(gap)
                last_ts[player_name] = ts

        player_records: list[dict[str, Any]] = []
        for player in game.players:
            if not is_pilot_player(player):
                continue
            key = _player_key(player.model, player.reasoningEffort)
            durations = player_latencies.get(player.name)
            if durations is not None:
                durations.sort()
                latency_p50 = round(durations[len(durations) // 2], 1)
            else:
                latency_p50 = None
            player_records.append(
                {
                    "key": key,
                    "modelName": _build_model_metadata(key, model_registry)["modelName"],
                    "won": winner == player.name,
                    "timedOut": bool(player.timedOut),
                    "costUsd": round(player.totalCostUsd or 0.0, 4),
                    "promptTokens": player_prompt_tokens.get(player.name, 0),
                    "completionTokens": player_completion_tokens.get(player.name, 0),
                    "cachedTokens": player_cached_tokens.get(player.name, 0),
                    "reasoningTokens": player_reasoning_tokens.get(player.name, 0),
                    "toolCallsOk": player.toolCallsOk,
                    "toolCallsFailed": player.toolCallsFailed,
                    "thinkingTimeSecs": round(player.thinkingTimeSecs, 1),
                    "responses": player_responses.get(player.name, 0),
                    "timeouts": player_timeouts.get(player.name, 0),
                    "otherErrors": player_other_errors.get(player.name, 0),
                    "contextResets": player_context_resets.get(player.name, 0),
                    "latencyP50": latency_p50,
                }
            )

        games_out.append(
            {
                "id": game.id,
                "ts": _parse_game_timestamp(game.timestamp),
                "epoch": game.harnessEpoch,
                "format": derive_format(game),
                "players": player_records,
            }
        )

    games_out.sort(key=lambda game: game["ts"])

    output: dict[str, Any] = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "games": games_out,
    }

    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / "internals-data.json"
    write_if_changed(output_path, json.dumps(output, indent=2) + "\n")
    return output_path


def generate_blunder_stats(data_dir: Path) -> Path:
    """Read blunder-stats.jsonl and write blunder-internals.json."""
    jsonl_path = data_dir / "blunder-stats.jsonl"
    records: list[dict[str, Any]] = []
    if jsonl_path.exists():
        for line in jsonl_path.read_text().splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))

    records.sort(key=lambda record: record["ts"] if "ts" in record else "")

    output: dict[str, Any] = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "runs": records,
    }

    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / "blunder-internals.json"
    write_if_changed(output_path, json.dumps(output, indent=2) + "\n")
    return output_path
