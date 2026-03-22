"""Compatibility wrapper for `magebench.game.export_llm_events`."""

from magebench.game.export_llm_events import (
    compute_thinking_time,
    compute_tool_call_counts,
    read_llm_events,
)

__all__ = ["compute_thinking_time", "compute_tool_call_counts", "read_llm_events"]
