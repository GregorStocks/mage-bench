"""Game export wire-format helpers."""

from __future__ import annotations

import copy
from collections.abc import Mapping

JsonValue = object
JsonObject = dict[str, JsonValue]

CURRENT_GAME_EXPORT_VERSION = 9


def _copy_dict(value: object) -> JsonObject:
    assert isinstance(value, Mapping), f"Expected object, got {value!r}"
    return {str(key): copy.deepcopy(item) for key, item in value.items()}


def migrate_game_export_to_current(data: Mapping[str, JsonValue]) -> JsonObject:
    """Require the current v9 snake_case export wire format."""
    migrated = _copy_dict(data)
    version = data.get("version")
    assert isinstance(version, int), f"game export version must be an int, got {version!r}"
    assert version == CURRENT_GAME_EXPORT_VERSION, (
        f"Unsupported game export version {version}; expected {CURRENT_GAME_EXPORT_VERSION}"
    )
    return migrated
