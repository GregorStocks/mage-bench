"""Compatibility wrapper for `magebench.game.export_decisions`.

TODO(shim): expires=issue:python-migration-step12 Delete this wrapper once
callers import `magebench.game.export_decisions` directly.
"""

from magebench.game import export_decisions as _export_decisions

build_decisions = _export_decisions.build_decisions
