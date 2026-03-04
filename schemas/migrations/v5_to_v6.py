"""Migration: v5 -> v6 (remove llmTrace).

llmTrace stored raw OpenAI-format API request/response objects for every LLM
call.  All useful data (reasoning text, tool calls, token counts, costs) is
already captured in llmEvents, so llmTrace is pure redundancy — typically 33%
of a game export's file size.

The down migration restores an empty llmTrace array.  The original trace data
is not recoverable from the export alone, but the field was never read by any
consumer so this is acceptable.
"""

SOURCE_VERSION = 5
TARGET_VERSION = 6


def up(data: dict) -> dict:
    """Migrate from v5 to v6: remove llmTrace."""
    assert data["version"] == 5, f"Expected v5, got v{data['version']}"

    del data["llmTrace"]

    data["version"] = 6
    return data


def down(data: dict) -> dict:
    """Migrate from v6 to v5: restore empty llmTrace."""
    assert data["version"] == 6, f"Expected v6, got v{data['version']}"

    data["llmTrace"] = []

    data["version"] = 5
    return data
