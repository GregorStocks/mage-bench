"""Compatibility wrapper for `magebench.game.export_decisions`.

TODO(shim): expires=issue:python-migration-step12 Delete this wrapper once
callers import `magebench.game.export_decisions` directly.
"""

from magebench.game.export_decisions import build_decisions

__all__ = ["build_decisions"]
