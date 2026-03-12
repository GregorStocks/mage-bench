"""Migration: v6 -> v7 (normalize player stats, require season/tournament).

Version 6 allowed exports where player summaries omitted tool call counts and
thinking time, even though that data was derivable from llmEvents. The website
papered over those historical gaps at read time.

Version 7 bakes the normalized fields into the export so readers can fail fast
and consume a single shape.
"""

SOURCE_VERSION = 6
TARGET_VERSION = 7


def up(data: dict) -> dict:
    """Migrate from v6 to v7: normalize player stats and top-level season data."""
    assert data["version"] == 6, f"Expected v6, got v{data['version']}"

    from schemas.migrations.v3_to_v4 import compute_season
    from scripts.export_game import compute_thinking_time, compute_tool_call_counts

    if "season" not in data:
        data["season"] = compute_season(data["harnessEpoch"])
    if "tournament" not in data:
        data["tournament"] = None

    tool_counts = compute_tool_call_counts(data.get("llmEvents", []))
    thinking = compute_thinking_time(data.get("llmEvents", []))

    for index, player in enumerate(data.get("players", [])):
        assert "name" in player, f"Player {index} missing 'name'"
        name = player["name"]
        ok, failed = tool_counts.get(name, (0, 0))
        if "toolCallsOk" not in player:
            player["toolCallsOk"] = ok
        if "toolCallsFailed" not in player:
            player["toolCallsFailed"] = failed
        if "thinkingTimeSecs" not in player:
            player["thinkingTimeSecs"] = round(thinking.get(name, 0.0), 1)

    data["version"] = 7
    return data


def down(data: dict) -> dict:
    """Migrate from v7 to v6.

    The v6 schema allowed these normalized player stats to be absent, but once
    v7 fills them there is no reliable way to recover whether a zero came from
    the original export or from normalization. Keep the enriched fields and only
    lower the version tag.
    """
    assert data["version"] == 7, f"Expected v7, got v{data['version']}"

    data["version"] = 6
    return data
