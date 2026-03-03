"""Validate the game-export JSON Schema itself (structure, rejects bad input).

Full per-game validation is in test_weird_conventions.py::TestAllExportsValid.
"""

import json
from pathlib import Path

import jsonschema

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "game-export-v2.schema.json"


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


class TestExportSchema:
    def test_schema_is_valid_json_schema(self) -> None:
        schema = _load_schema()
        jsonschema.Draft7Validator.check_schema(schema)

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
        assert any("version" in str(e.absolute_path) or "enum" in e.message for e in errors)

    def test_schema_accepts_version_3(self) -> None:
        schema = _load_schema()
        validator = jsonschema.Draft7Validator(schema)
        v3 = {
            "version": 3,
            "id": "test_v3",
            "timestamp": "",
            "gameType": "",
            "deckType": "",
            "totalTurns": 0,
            "winner": None,
            "harnessEpoch": 0,
            "youtubeUrl": "",
            "players": [],
            "cardImages": {},
            "cardData": {
                "Lightning Bolt": {
                    "mana_cost": "{R}",
                    "type_line": "Instant",
                    "oracle_text": "Lightning Bolt deals 3 damage to any target.",
                }
            },
            "snapshots": [],
            "actions": [],
            "llmEvents": [],
            "llmTrace": [],
            "gameOver": None,
            "annotations": [],
            "blunderScriptVersion": 0,
        }
        errors = list(validator.iter_errors(v3))
        assert errors == [], f"v3 export should be valid, got: {errors}"

    def test_schema_accepts_version_2_without_card_data(self) -> None:
        schema = _load_schema()
        validator = jsonschema.Draft7Validator(schema)
        v2 = {
            "version": 2,
            "id": "test_v2",
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
        errors = list(validator.iter_errors(v2))
        assert errors == [], f"v2 export should still be valid, got: {errors}"

    def test_schema_rejects_missing_required_field(self) -> None:
        schema = _load_schema()
        validator = jsonschema.Draft7Validator(schema)
        bad = {"version": 2}
        errors = list(validator.iter_errors(bad))
        assert len(errors) > 0
