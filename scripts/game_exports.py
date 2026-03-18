"""Shared raw helpers for game export files.

These helpers intentionally avoid schema validation so migration and backfill
scripts can operate on older export versions.
"""

import gzip
import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
GAMES_DIR = REPO_ROOT / "website" / "public" / "games"
GAME_EXPORT_GZ_THRESHOLD = 25 * 1024 * 1024


def _assert_game_export_path(path: Path) -> None:
    assert path.name.endswith(".json") or path.name.endswith(".json.gz"), (
        f"Expected game export path ending in .json or .json.gz, got {path}"
    )


def _base_game_export_path(path: Path) -> Path:
    _assert_game_export_path(path)
    return path.with_suffix("") if path.suffix == ".gz" else path


def load_raw_game_export(path: str | Path) -> dict[str, Any]:
    """Load a game export without validating it against the current schema."""
    export_path = Path(path)
    _assert_game_export_path(export_path)
    raw = (
        gzip.decompress(export_path.read_bytes())
        if export_path.suffix == ".gz"
        else export_path.read_text()
    )
    data = json.loads(raw)
    assert isinstance(data, dict), f"{export_path}: expected JSON object"
    return data


def write_raw_game_export(
    path: str | Path,
    data: Mapping[str, Any],
    *,
    compress: bool | None = None,
) -> Path:
    """Write a game export and remove the alternate .json/.json.gz variant."""
    export_path = Path(path)
    _assert_game_export_path(export_path)

    json_bytes = json.dumps(
        _jsonify_export_payload(data), indent=2, ensure_ascii=False
    ).encode()
    if compress is None:
        compress = len(json_bytes) > GAME_EXPORT_GZ_THRESHOLD

    base_path = _base_game_export_path(export_path)
    json_path = base_path.with_suffix(".json")
    gz_path = base_path.with_suffix(".json.gz")
    if compress:
        gz_path.write_bytes(gzip.compress(json_bytes))
        if json_path.exists():
            json_path.unlink()
        return gz_path

    json_path.write_bytes(json_bytes)
    if gz_path.exists():
        gz_path.unlink()
    return json_path


def _jsonify_export_payload(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        source_keys: frozenset[str] | None = getattr(value, "_source_keys", None)
        if source_keys is not None:
            source_result: dict[str, Any] = {}
            for field in fields(value):
                if field.name not in source_keys:
                    continue
                source_result[_json_field_name(field.name)] = _jsonify_export_payload(
                    getattr(value, field.name)
                )
            extra: Mapping[str, Any] | None = getattr(value, "_extra", None)
            if extra:
                for key, extra_value in extra.items():
                    assert key not in source_result, (
                        f"duplicate dataclass export key {key!r}"
                    )
                    source_result[str(key)] = _jsonify_export_payload(extra_value)
            return source_result
        result: dict[str, Any] = {}
        extras: Mapping[str, Any] = {}
        for field in fields(value):
            field_value = getattr(value, field.name)
            if field.name == "_extras":
                # Dataclass loaders keep unknown JSON properties under `_extras`;
                # flatten them back into the emitted object here.
                assert isinstance(field_value, Mapping), (
                    f"dataclass _extras must be a mapping, got {field_value!r}"
                )
                extras = field_value
                continue
            if field_value is None:
                continue
            result[_json_field_name(field.name)] = _jsonify_export_payload(field_value)
        for key, extra_value in extras.items():
            assert key not in result, f"duplicate dataclass export key {key!r}"
            result[str(key)] = _jsonify_export_payload(extra_value)
        return result
    if isinstance(value, list):
        return [_jsonify_export_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonify_export_payload(item) for key, item in value.items()}
    return value


def _json_field_name(field_name: str) -> str:
    if field_name == "from_":
        return "from"
    return field_name


def glob_game_export_paths(games_dir: Path = GAMES_DIR) -> list[Path]:
    """List game export files, preferring .json.gz when both variants exist."""
    gz_files = set(games_dir.glob("game_*.json.gz"))
    gz_stems = {path.name.removesuffix(".gz") for path in gz_files}
    json_files = [
        path for path in games_dir.glob("game_*.json") if path.name not in gz_stems
    ]
    return sorted(gz_files | set(json_files))
