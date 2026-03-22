"""Compatibility wrapper for `magebench.game.export_errors`.

TODO(shim): expires=issue:python-migration-step12 Delete this wrapper once
callers import `magebench.game.export_errors` directly.
"""

from magebench.game.export_errors import link_errors_to_decisions, read_errors

__all__ = ["link_errors_to_decisions", "read_errors"]
