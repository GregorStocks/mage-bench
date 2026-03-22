"""Compatibility wrapper for `magebench.game.game_exports`.

TODO(shim): expires=issue:python-migration-step12 Delete this wrapper once
callers import `magebench.game.game_exports` directly.
"""

from magebench.game import game_exports as _game_exports

GAME_EXPORT_GZ_THRESHOLD = _game_exports.GAME_EXPORT_GZ_THRESHOLD
GAMES_DIR = _game_exports.GAMES_DIR
glob_game_export_paths = _game_exports.glob_game_export_paths
load_raw_game_export = _game_exports.load_raw_game_export
write_raw_game_export = _game_exports.write_raw_game_export
