"""Compatibility wrapper for `magebench.game.scryfall`."""

from magebench.game.scryfall import (
    collection,
    extract_oracle_fields,
    get_oracle_texts,
    named,
    resolve_cards,
    search,
    search_token,
)

__all__ = [
    "collection",
    "extract_oracle_fields",
    "get_oracle_texts",
    "named",
    "resolve_cards",
    "search",
    "search_token",
]
