"""Compatibility wrapper for `magebench.game.game_exports`.

TODO(shim): expires=issue:python-migration-step12 Delete this wrapper once
callers import `magebench.game.game_exports` directly.
"""

from magebench.game.game_exports import (
    GAME_EXPORT_GZ_THRESHOLD,
    GAMES_DIR,
    glob_game_export_paths,
    load_raw_game_export,
    write_raw_game_export,
)

__all__ = [
    "GAMES_DIR",
    "GAME_EXPORT_GZ_THRESHOLD",
    "glob_game_export_paths",
    "load_raw_game_export",
    "write_raw_game_export",
]
