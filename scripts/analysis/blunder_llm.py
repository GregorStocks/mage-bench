"""Compatibility wrapper for the canonical blunder-LLM module."""

# TODO(shim): expires=issue:python-migration-step12 Delete this wrapper once
# callers import magebench.analysis.blunder.blunder_llm directly.
from magebench.analysis.blunder import blunder_llm as _impl


def __getattr__(name: str) -> object:
    return getattr(_impl, name)
