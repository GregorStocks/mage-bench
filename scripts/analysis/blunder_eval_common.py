"""Compatibility wrapper for the canonical blunder-eval-common module."""

# TODO(shim): expires=issue:python-migration-step12 Delete this wrapper once
# callers import magebench.analysis.blunder.blunder_eval_common directly.
from magebench.analysis.blunder import blunder_eval_common as _impl


def __getattr__(name: str) -> object:
    return getattr(_impl, name)
