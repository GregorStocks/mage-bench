"""Compatibility wrapper for `magebench.game.export_card_data`.

TODO(shim): expires=issue:python-migration-step12 Delete this wrapper once
callers import `magebench.game.export_card_data` directly.
"""

from magebench.game.export_card_data import (
    DECKLIST_RE,
    build_card_data,
)

__all__ = ["DECKLIST_RE", "build_card_data"]
