"""Validate all exported games against the JSON Schema."""

import gzip
import json
from pathlib import Path

import jsonschema
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "game-export-v2.schema.json"
GAMES_DIR = REPO_ROOT / "website" / "public" / "games"


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def _glob_game_files() -> list[Path]:
    """Find all game export files, preferring .json.gz over .json."""
    gz_files = set(GAMES_DIR.glob("game_*.json.gz"))
    gz_stems = {p.name.removesuffix(".gz") for p in gz_files}
    json_files = [p for p in GAMES_DIR.glob("game_*.json") if p.name not in gz_stems]
    return sorted(gz_files | set(json_files))


def _load_game(path: Path) -> dict:
    if path.suffix == ".gz":
        with gzip.open(path, "rt") as f:
            return json.load(f)
    with open(path) as f:
        return json.load(f)


class TestExportSchema:
    def test_schema_is_valid_json_schema(self) -> None:
        schema = _load_schema()
        jsonschema.Draft7Validator.check_schema(schema)

    def test_all_exports_conform(self) -> None:
        schema = _load_schema()
        validator = jsonschema.Draft7Validator(schema)
        game_files = _glob_game_files()
        assert len(game_files) > 0, "No game files found in website/public/games/"

        failures: list[str] = []
        for path in game_files:
            data = _load_game(path)
            errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
            if errors:
                err_path = "/".join(str(p) for p in errors[0].absolute_path)
                failures.append(f"{path.name}: {errors[0].message} (at {err_path})")

        assert not failures, f"{len(failures)} game(s) failed validation:\n" + "\n".join(failures[:10])

    def test_schema_rejects_invalid_version(self) -> None:
        schema = _load_schema()
        validator = jsonschema.Draft7Validator(schema)
        # Minimal valid-ish structure but with wrong version
        bad = {
            "version": 1,
            "id": "test",
            "timestamp": "",
            "gameType": "",
            "deckType": "",
            "totalTurns": 0,
            "winner": None,
            "harnessEpoch": 0,
            "youtubeUrl": "",
            "players": [],
            "cardImages": {},
            "snapshots": [],
            "actions": [],
            "llmEvents": [],
            "llmTrace": [],
            "gameOver": None,
            "annotations": [],
            "blunderScriptVersion": 0,
        }
        errors = list(validator.iter_errors(bad))
        assert any("version" in str(e.absolute_path) or "const" in e.message for e in errors)

    def test_schema_rejects_missing_required_field(self) -> None:
        schema = _load_schema()
        validator = jsonschema.Draft7Validator(schema)
        bad = {"version": 2}
        errors = list(validator.iter_errors(bad))
        assert len(errors) > 0

    @pytest.mark.parametrize(
        "game_file",
        _glob_game_files()[:5],
        ids=lambda p: p.name,
    )
    def test_sample_games_conform(self, game_file: Path) -> None:
        """Parameterized test for first 5 games — gives per-game failure messages."""
        schema = _load_schema()
        validator = jsonschema.Draft7Validator(schema)
        data = _load_game(game_file)
        errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
        assert not errors, f"{errors[0].message} (at {'/'.join(str(p) for p in errors[0].absolute_path)})"
