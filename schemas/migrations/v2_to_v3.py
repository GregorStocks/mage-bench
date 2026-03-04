"""Migration: v2 -> v3 (add cardData and token images)."""

import sys
from pathlib import Path

SOURCE_VERSION = 2
TARGET_VERSION = 3

# Ensure scripts/ is importable for export_game helpers
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def _is_token_key(name: str) -> bool:
    """Check if a cardImages key is a token (added by v3 migration)."""
    return " Token" in name or " token" in name


def up(data: dict) -> dict:
    """Migrate from v2 to v3: add cardData and token images via Scryfall."""
    assert data["version"] == 2, f"Expected v2, got v{data['version']}"

    from export_game import _build_card_data

    card_images, card_data = _build_card_data(
        data.get("cardImages", {}),
        data.get("snapshots", []),
    )
    data["cardImages"] = card_images
    data["cardData"] = card_data
    data["version"] = 3
    return data


def down(data: dict) -> dict:
    """Migrate from v3 to v2: remove cardData and token images."""
    assert data["version"] == 3, f"Expected v3, got v{data['version']}"

    data.pop("cardData", None)
    card_images = data.get("cardImages", {})
    data["cardImages"] = {k: v for k, v in card_images.items() if not _is_token_key(k)}
    data["version"] = 2
    return data
