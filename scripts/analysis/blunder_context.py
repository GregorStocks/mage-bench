"""Compatibility wrapper for the canonical blunder-context module."""

# TODO(shim): expires=issue:python-migration-step12 Delete this wrapper once
# callers import magebench.analysis.blunder.blunder_context directly.
from magebench.analysis.blunder import blunder_context as _impl


def __getattr__(name: str) -> object:
    return getattr(_impl, name)
