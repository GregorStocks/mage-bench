"""Compatibility wrapper for the canonical blunder-prompts module."""

# TODO(shim): expires=issue:python-migration-step12 Delete this wrapper once
# callers import magebench.analysis.blunder.blunder_prompts directly.
from magebench.analysis.blunder import blunder_prompts as _impl


def __getattr__(name: str) -> object:
    return getattr(_impl, name)
