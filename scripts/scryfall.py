"""Compatibility wrapper for `magebench.game.scryfall`.

TODO(shim): expires=issue:python-migration-step12 Delete this wrapper once
callers import `magebench.game.scryfall` directly.
"""

from magebench.game import scryfall as _scryfall

collection = _scryfall.collection
extract_oracle_fields = _scryfall.extract_oracle_fields
get_oracle_texts = _scryfall.get_oracle_texts
named = _scryfall.named
resolve_cards = _scryfall.resolve_cards
search = _scryfall.search
search_token = _scryfall.search_token
