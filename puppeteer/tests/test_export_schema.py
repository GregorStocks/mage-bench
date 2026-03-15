"""Validate the per-version game-export JSON Schemas (structure, rejects bad input).

Full per-game validation is in test_weird_conventions.py::TestAllExportsValid.
"""

import gzip
import json
from pathlib import Path

import jsonschema
import pytest

from schemas.game_export_types import (
    Action,
    Annotation,
    BuiltGameExport,
    CardMetadata,
    CombatGroup,
    Decision,
    GameError,
    GameExport,
    GameOver,
    LlmEvent,
    LlmUsage,
    PilotContext,
    Player,
    Snapshot,
    SnapshotPlayer,
    load_built_game_export,
    load_game_export,
    require_built_game_export,
)

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
        "gameType": "Two Player Duel",
        "deckType": "Constructed - Standard",
        "totalTurns": 0,
        "winner": None,
        "harnessEpoch": 0,
        "youtubeUrl": "",
        "players": [],
        "cardImages": {},
        "snapshots": [],
        "actions": [],
        "llmEvents": [],
        "gameOver": None,
        "annotations": [],
        "blunderScriptVersion": 0,
    }
    # v5 and earlier require llmTrace
    if version <= 5:
        base["llmTrace"] = []
    base.update(overrides)
    return base


def _typed_dict_keys(typed_dict_cls: object) -> set[str]:
    return set(typed_dict_cls.__required_keys__) | set(typed_dict_cls.__optional_keys__)


def _assert_typed_dict_matches_schema(
    typed_dict_cls: object,
    *,
    schema: dict,
    required_override: set[str] | None = None,
) -> None:
    expected_props = set(schema["properties"])
    expected_required = required_override if required_override is not None else set(schema.get("required", []))
    assert _typed_dict_keys(typed_dict_cls) == expected_props
    assert set(typed_dict_cls.__required_keys__) == expected_required
    assert set(typed_dict_cls.__optional_keys__) == expected_props - expected_required


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

    def test_v5_schema_is_valid(self) -> None:
        schema = _load_schema(5)
        jsonschema.Draft7Validator.check_schema(schema)

    def test_v5_schema_accepts_v5(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(5))
        v5 = _minimal_export(5, season=1, tournament=None)
        errors = list(validator.iter_errors(v5))
        assert errors == [], f"v5 should be valid: {errors}"

    def test_v5_schema_rejects_v4(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(5))
        errors = list(validator.iter_errors(_minimal_export(4)))
        assert errors, "v5 schema should reject version 4"

    def test_v6_schema_is_valid(self) -> None:
        schema = _load_schema(6)
        jsonschema.Draft7Validator.check_schema(schema)

    def test_v6_schema_accepts_v6(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(6))
        v6 = _minimal_export(6, season=1, tournament=None)
        errors = list(validator.iter_errors(v6))
        assert errors == [], f"v6 should be valid: {errors}"

    def test_v6_schema_rejects_v5(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(6))
        errors = list(validator.iter_errors(_minimal_export(5)))
        assert errors, "v6 schema should reject version 5"

    def test_v6_schema_rejects_llmTrace(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(6))
        v6_with_trace = _minimal_export(6, season=1, tournament=None, llmTrace=[])
        errors = list(validator.iter_errors(v6_with_trace))
        assert errors, "v6 schema should reject exports with llmTrace"

    def test_v6_schema_rejects_empty_format_fields(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(6))
        v6 = _minimal_export(6, season=1, tournament=None, gameType="", deckType="")
        errors = list(validator.iter_errors(v6))
        assert errors, "v6 schema should reject empty gameType/deckType"

    def test_v7_schema_is_valid(self) -> None:
        schema = _load_schema(7)
        jsonschema.Draft7Validator.check_schema(schema)

    def test_v7_schema_accepts_v7(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(7))
        v7 = _minimal_export(
            7,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "toolCallsOk": 3,
                    "toolCallsFailed": 1,
                    "thinkingTimeSecs": 12.5,
                }
            ],
        )
        errors = list(validator.iter_errors(v7))
        assert errors == [], f"v7 should be valid: {errors}"

    def test_v7_schema_rejects_v6(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(7))
        errors = list(validator.iter_errors(_minimal_export(6, season=1, tournament=None)))
        assert errors, "v7 schema should reject version 6"

    def test_v7_schema_rejects_player_missing_stats(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(7))
        v7 = _minimal_export(
            7,
            season=1,
            tournament=None,
            players=[{"name": "Alice", "type": "pilot"}],
        )
        errors = list(validator.iter_errors(v7))
        assert errors, "v7 schema should reject players without normalized stats"

    def test_v7_schema_rejects_empty_format_fields(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(7))
        v7 = _minimal_export(7, season=1, tournament=None, gameType="", deckType="")
        errors = list(validator.iter_errors(v7))
        assert errors, "v7 schema should reject empty gameType/deckType"

    def test_schema_rejects_missing_required_field(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(5))
        bad = {"version": 5}
        errors = list(validator.iter_errors(bad))
        assert len(errors) > 0

    def test_schema_backed_typed_dicts_match_v7_schema(self) -> None:
        schema = _load_schema(7)
        defs = schema["$defs"]

        _assert_typed_dict_matches_schema(GameExport, schema=schema)
        _assert_typed_dict_matches_schema(
            BuiltGameExport,
            schema=schema,
            required_override=set(schema["required"]) - {"annotations", "blunderScriptVersion"},
        )
        _assert_typed_dict_matches_schema(Player, schema=defs["Player"])
        _assert_typed_dict_matches_schema(Snapshot, schema=defs["Snapshot"])
        _assert_typed_dict_matches_schema(SnapshotPlayer, schema=defs["SnapshotPlayer"])
        _assert_typed_dict_matches_schema(CombatGroup, schema=defs["CombatGroup"])
        _assert_typed_dict_matches_schema(Action, schema=defs["Action"])
        _assert_typed_dict_matches_schema(LlmEvent, schema=defs["LlmEvent"])
        _assert_typed_dict_matches_schema(LlmUsage, schema=defs["LlmUsage"])
        _assert_typed_dict_matches_schema(GameOver, schema=defs["GameOver"])
        _assert_typed_dict_matches_schema(Annotation, schema=defs["Annotation"])
        _assert_typed_dict_matches_schema(Decision, schema=defs["Decision"])
        _assert_typed_dict_matches_schema(PilotContext, schema=defs["PilotContext"])
        _assert_typed_dict_matches_schema(GameError, schema=defs["GameError"])
        _assert_typed_dict_matches_schema(CardMetadata, schema=defs["CardMetadata"])

    def test_typed_loader_accepts_minimal_v7_export(self, tmp_path: Path) -> None:
        path = tmp_path / "game_v7.json"
        payload = _minimal_export(
            7,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "toolCallsOk": 3,
                    "toolCallsFailed": 1,
                    "thinkingTimeSecs": 12.5,
                }
            ],
        )
        path.write_text(json.dumps(payload))

        game = load_game_export(path)

        assert game["version"] == 7
        assert game["players"][0]["toolCallsOk"] == 3
        assert game["annotations"] == []

    def test_typed_loader_accepts_gzipped_exports(self, tmp_path: Path) -> None:
        path = tmp_path / "game_v7.json.gz"
        payload = _minimal_export(
            7,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "toolCallsOk": 0,
                    "toolCallsFailed": 0,
                    "thinkingTimeSecs": 0.0,
                }
            ],
        )
        path.write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))

        game = load_game_export(path)

        assert game["id"] == "test_v7"

    def test_typed_loader_rejects_unannotated_export(self, tmp_path: Path) -> None:
        path = tmp_path / "game_v7.json"
        payload = _minimal_export(7, season=1, tournament=None)
        del payload["annotations"]
        path.write_text(json.dumps(payload))

        with pytest.raises(AssertionError, match="annotations"):
            load_game_export(path)

    def test_built_export_validator_allows_missing_annotation_fields(self) -> None:
        payload = _minimal_export(
            7,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "toolCallsOk": 1,
                    "toolCallsFailed": 0,
                    "thinkingTimeSecs": 2.0,
                }
            ],
        )
        del payload["annotations"]
        del payload["blunderScriptVersion"]

        built = require_built_game_export(payload, source="built export")

        assert built["season"] == 1
        assert "annotations" not in built

    def test_built_loader_accepts_unannotated_export(self, tmp_path: Path) -> None:
        path = tmp_path / "built_v7.json"
        payload = _minimal_export(
            7,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "toolCallsOk": 1,
                    "toolCallsFailed": 0,
                    "thinkingTimeSecs": 2.0,
                }
            ],
        )
        del payload["annotations"]
        del payload["blunderScriptVersion"]
        path.write_text(json.dumps(payload))

        built = load_built_game_export(path)

        assert built["version"] == 7
        assert "annotations" not in built

    def test_loader_accepts_empty_decision_strings_allowed_by_schema(self, tmp_path: Path) -> None:
        path = tmp_path / "empty_decision_strings.json"
        payload = _minimal_export(
            7,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "toolCallsOk": 0,
                    "toolCallsFailed": 0,
                    "thinkingTimeSecs": 0.0,
                }
            ],
            decisions=[
                {
                    "index": 0,
                    "snapshotIndex": 0,
                    "player": "Alice",
                    "turn": 1,
                    "phase": None,
                    "actionType": "",
                    "responseType": "",
                    "message": "",
                    "choices": [],
                    "choiceCount": 0,
                    "isForced": True,
                    "llmEventIndices": [],
                    "subsequentActions": [],
                }
            ],
        )
        path.write_text(json.dumps(payload))

        game = load_game_export(path)

        assert game["decisions"][0]["actionType"] == ""
        assert game["decisions"][0]["responseType"] == ""
        assert game["decisions"][0]["message"] == ""
