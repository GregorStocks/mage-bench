"""Shared raw helpers for game export files.

These helpers intentionally avoid schema validation so backfill and repair
scripts can rewrite committed exports without going through typed loaders.
"""

import gzip
from collections.abc import Mapping
from dataclasses import is_dataclass
from pathlib import Path
from typing import Any

from magebench.common.json5_utils import loads_json5
from magebench.common.json5_writer import dumps_json5
from magebench.game.game_export_types import BuiltGameExport, GameExport, json_default
from schemas.game_export_migrations import migrate_game_export_to_current

REPO_ROOT = Path(__file__).resolve().parents[3]
GAMES_DIR = REPO_ROOT / "website" / "public" / "games"
GAME_EXPORT_GZ_THRESHOLD = 25 * 1024 * 1024

_VALID_EXTENSIONS = {".json", ".json.gz", ".json5", ".json5.gz"}


def _assert_game_export_path(path: Path) -> None:
    for ext in _VALID_EXTENSIONS:
        if path.name.endswith(ext):
            return
    raise AssertionError(
        f"Expected game export path ending in {_VALID_EXTENSIONS}, got {path}"
    )


def _base_game_export_path(path: Path) -> Path:
    _assert_game_export_path(path)
    return path.with_suffix("") if path.suffix == ".gz" else path


def load_raw_game_export(path: str | Path) -> dict[str, Any]:
    """Load a game export in the current wire format without schema validation."""
    export_path = Path(path)
    _assert_game_export_path(export_path)
    raw = (
        gzip.decompress(export_path.read_bytes()).decode()
        if export_path.suffix == ".gz"
        else export_path.read_text()
    )
    data = loads_json5(raw)
    assert isinstance(data, dict), f"{export_path}: expected JSON object"
    return migrate_game_export_to_current(data)


def write_raw_game_export(
    path: str | Path,
    data: Mapping[str, Any] | GameExport | BuiltGameExport,
    *,
    compress: bool | None = None,
) -> Path:
    """Write a game export as JSON5, removing alternate variants."""
    export_path = Path(path)
    _assert_game_export_path(export_path)

    json5_bytes = dumps_json5(
        _jsonify_export_payload(data), ensure_ascii=False
    ).encode()
    if compress is None:
        compress = len(json5_bytes) > GAME_EXPORT_GZ_THRESHOLD

    base_path = _base_game_export_path(export_path)
    # Strip the .json or .json5 suffix to get the stem for building new paths
    stem = base_path.with_suffix("")
    json5_path = stem.with_suffix(".json5")
    json5_gz_path = Path(str(json5_path) + ".gz")

    # Clean up all old variants
    for old_ext in (".json", ".json.gz", ".json5", ".json5.gz"):
        old_path = Path(str(stem) + old_ext)
        if old_path.exists() and old_path != json5_path and old_path != json5_gz_path:
            old_path.unlink()

    if compress:
        json5_gz_path.write_bytes(gzip.compress(json5_bytes))
        if json5_path.exists():
            json5_path.unlink()
        return json5_gz_path

    json5_path.write_bytes(json5_bytes)
    if json5_gz_path.exists():
        json5_gz_path.unlink()
    return json5_path


def _jsonify_export_payload(value: object) -> object:
    """Recursively convert dataclass-backed export data to plain JSON-ready dicts."""
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonify_export_payload(json_default(value))
    if isinstance(value, list):
        return [_jsonify_export_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonify_export_payload(item) for key, item in value.items()}
    return value


def glob_game_export_paths(games_dir: Path = GAMES_DIR) -> list[Path]:
    """List game export files, preferring .json5.gz when both variants exist."""
    gz_files = set(games_dir.glob("game_*.json5.gz"))
    gz_stems = {path.name.removesuffix(".gz") for path in gz_files}
    json5_files = [
        path for path in games_dir.glob("game_*.json5") if path.name not in gz_stems
    ]
    return sorted(gz_files | set(json5_files))
