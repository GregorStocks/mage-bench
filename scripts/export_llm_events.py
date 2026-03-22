"""Compatibility wrapper for `magebench.game.export_llm_events`.

TODO(shim): expires=issue:python-migration-step12 Delete this wrapper once
callers import `magebench.game.export_llm_events` directly.
"""

from magebench.game import export_llm_events as _export_llm_events

compute_thinking_time = _export_llm_events.compute_thinking_time
compute_tool_call_counts = _export_llm_events.compute_tool_call_counts
read_llm_events = _export_llm_events.read_llm_events
