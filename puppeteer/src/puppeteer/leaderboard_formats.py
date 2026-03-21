"""Format bucketing helpers for leaderboard generation."""

from __future__ import annotations

from collections.abc import Mapping

from schemas.game_export_types import BuiltGameExport, GameExport

_DECK_TYPE_TO_FORMAT: dict[str, str] = {
    "Constructed - Standard": "standard",
    "Constructed - Modern": "modern",
    "Constructed - Legacy": "legacy",
    "Variant Magic - Freeform Commander": "commander",
    "Variant Magic - Commander": "commander",
    "Limited": "jumpstart",
}

FORMAT_LABELS: dict[str, str] = {
    "jumpstart": "Jumpstart",
    "standard": "Standard",
    "modern": "Modern",
    "legacy": "Legacy",
    "commander": "Commander (Exhibition)",
    "combined": "Combined",
}

RATED_POOLS = ("jumpstart", "standard", "modern", "legacy")
EXHIBITION_POOLS = ("commander",)
FORMAT_POOLS = RATED_POOLS + EXHIBITION_POOLS


def derive_format(game: Mapping[str, object] | GameExport | BuiltGameExport) -> str:
    """Derive canonical format name from game data."""
    if isinstance(game, (GameExport, BuiltGameExport)):
        deck_type: object = game.deck_type
        game_id: object = game.id
    else:
        deck_type = game.get("deckType")
        game_id = game.get("id", "<unknown>")
    assert isinstance(deck_type, str) and deck_type, f"Game {game_id} missing deckType"
    if deck_type in _DECK_TYPE_TO_FORMAT:
        return _DECK_TYPE_TO_FORMAT[deck_type]
    return deck_type.lower().replace(" ", "-")
