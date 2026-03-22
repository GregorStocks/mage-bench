"""Compatibility wrapper for `magebench.game.export_errors`.

TODO(shim): expires=issue:python-migration-step12 Delete this wrapper once
callers import `magebench.game.export_errors` directly.
"""

from magebench.game import export_errors as _export_errors

link_errors_to_decisions = _export_errors.link_errors_to_decisions
read_errors = _export_errors.read_errors
