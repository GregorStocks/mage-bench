"""Validate the per-version game-export JSON Schemas (structure, rejects bad input).

Full per-game validation is in test_weird_conventions.py::TestAllExportsValid.
"""

import json
from pathlib import Path

import jsonschema

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_DIR = REPO_ROOT / "schemas"


def _load_schema(version: int) -> dict:
    path = SCHEMA_DIR / f"game-export-v{version}.schema.json"
    return json.loads(path.read_text())


def _minimal_export(version: int, **overrides) -> dict:
    """Build a minimal valid export for the given version."""
    base = {
        "version": version,
        "id": f"test_v{version}",
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
    base.update(overrides)
    return base


class TestExportSchema:
    def test_v2_schema_is_valid(self) -> None:
        schema = _load_schema(2)
        jsonschema.Draft7Validator.check_schema(schema)

    def test_v3_schema_is_valid(self) -> None:
        schema = _load_schema(3)
        jsonschema.Draft7Validator.check_schema(schema)

    def test_v4_schema_is_valid(self) -> None:
        schema = _load_schema(4)
        jsonschema.Draft7Validator.check_schema(schema)

    def test_v2_schema_accepts_v2(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(2))
        errors = list(validator.iter_errors(_minimal_export(2)))
        assert errors == [], f"v2 should be valid: {errors}"

    def test_v2_schema_rejects_v3(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(2))
        errors = list(validator.iter_errors(_minimal_export(3)))
        assert errors, "v2 schema should reject version 3"

    def test_v3_schema_accepts_v3(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(3))
        v3 = _minimal_export(
            3,
            cardData={
                "Lightning Bolt": {
                    "mana_cost": "{R}",
                    "type_line": "Instant",
                    "oracle_text": "Lightning Bolt deals 3 damage to any target.",
                }
            },
        )
        errors = list(validator.iter_errors(v3))
        assert errors == [], f"v3 should be valid: {errors}"

    def test_v3_schema_rejects_v4(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(3))
        errors = list(validator.iter_errors(_minimal_export(4)))
        assert errors, "v3 schema should reject version 4"

    def test_v4_schema_accepts_v4_with_season_tournament(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(4))
        v4 = _minimal_export(
            4,
            harnessEpoch=40,
            season=1,
            tournament=None,
            cardData={
                "Lightning Bolt": {
                    "mana_cost": "{R}",
                    "type_line": "Instant",
                    "oracle_text": "Lightning Bolt deals 3 damage to any target.",
                }
            },
        )
        errors = list(validator.iter_errors(v4))
        assert errors == [], f"v4 should be valid: {errors}"

    def test_v4_schema_accepts_tournament_string(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(4))
        v4 = _minimal_export(4, season=1, tournament="season-1-championship")
        errors = list(validator.iter_errors(v4))
        assert errors == [], f"v4 with tournament string should be valid: {errors}"

    def test_v4_schema_rejects_v2(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(4))
        errors = list(validator.iter_errors(_minimal_export(2)))
        assert errors, "v4 schema should reject version 2"

    def test_schema_rejects_missing_required_field(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(4))
        bad = {"version": 4}
        errors = list(validator.iter_errors(bad))
        assert len(errors) > 0
