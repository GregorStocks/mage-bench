"""Compatibility wrapper for `magebench.game.export_card_data`.

TODO(shim): expires=issue:python-migration-step12 Delete this wrapper once
callers import `magebench.game.export_card_data` directly.
"""

from magebench.game import export_card_data as _export_card_data

DECKLIST_RE = _export_card_data.DECKLIST_RE
build_card_data = _export_card_data.build_card_data
