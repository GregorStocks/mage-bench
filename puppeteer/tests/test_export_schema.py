"""Validate the per-version game-export JSON Schemas (structure, rejects bad input).

Full per-game validation is in test_weird_conventions.py::TestAllExportsValid.
"""

import dataclasses
import gzip
import json
from pathlib import Path

import jsonschema
import pytest

from schemas.game_export_types import (
    Action,
    Annotation,
    AutoPilotModeEvent,
    BuiltGameExport,
    CardMetadata,
    Choice,
    CombatCreature,
    CombatGroup,
    ContextResetEvent,
    ContextTrimEvent,
    Decision,
    GameError,
    GameExport,
    GameOver,
    GameStartEvent,
    LlmErrorEvent,
    LlmResponseEvent,
    LlmUsage,
    MultiAmountItem,
    Permanent,
    PilotContext,
    PilotPlayer,
    Player,
    Snapshot,
    SnapshotPlayer,
    StackItem,
    StackTarget,
    StallEvent,
    ToolCallEvent,
    _validate_player,
    is_pilot_player,
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


def _dataclass_keys(
    cls: type,
    *,
    ignored_fields: set[str] | None = None,
    renames: dict[str, str] | None = None,
) -> set[str]:
    ignored = ignored_fields or set()
    renamed = renames or {}
    return {renamed.get(f.name, f.name) for f in dataclasses.fields(cls) if f.name not in ignored}


def _dataclass_required_keys(
    cls: type,
    *,
    ignored_fields: set[str] | None = None,
    renames: dict[str, str] | None = None,
) -> set[str]:
    ignored = ignored_fields or set()
    renamed = renames or {}
    return {
        renamed.get(f.name, f.name)
        for f in dataclasses.fields(cls)
        if f.name not in ignored and f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    }


def _assert_dataclass_matches_schema(
    cls: type,
    *,
    schema: dict,
    required_override: set[str] | None = None,
    extra_fields: set[str] | None = None,
    field_renames: dict[str, str] | None = None,
) -> None:
    expected_props = set(schema["properties"])
    expected_required = required_override if required_override is not None else set(schema.get("required", []))
    assert (
        _dataclass_keys(
            cls,
            ignored_fields=extra_fields,
            renames=field_renames,
        )
        == expected_props
    )
    assert (
        _dataclass_required_keys(
            cls,
            ignored_fields=extra_fields,
            renames=field_renames,
        )
        == expected_required
    )


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

    def test_v6_schema_rejects_llm_trace(self) -> None:
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
                    "model": "test/model",
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

    def test_v8_schema_is_valid(self) -> None:
        schema = _load_schema(8)
        jsonschema.Draft7Validator.check_schema(schema)

    def test_v8_schema_accepts_v8(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(8))
        v8 = _minimal_export(
            8,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model",
                    "toolCallsOk": 3,
                    "toolCallsFailed": 1,
                    "thinkingTimeSecs": 12.5,
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
            annotations=[
                {
                    "decisionIndex": 0,
                    "snapshotIndex": 0,
                    "player": "Alice",
                    "type": "blunder",
                    "severity": "minor",
                    "description": "Bad play",
                    "actionTaken": "Pass",
                    "betterLine": "Cast a threat",
                }
            ],
        )
        errors = list(validator.iter_errors(v8))
        assert errors == [], f"v8 should be valid: {errors}"

    def test_v8_schema_rejects_v7(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(8))
        errors = list(validator.iter_errors(_minimal_export(7, season=1, tournament=None)))
        assert errors, "v8 schema should reject version 7"

    def test_v8_schema_requires_annotation_decision_index(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(8))
        v8 = _minimal_export(
            8,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model",
                    "toolCallsOk": 3,
                    "toolCallsFailed": 1,
                    "thinkingTimeSecs": 12.5,
                }
            ],
            annotations=[
                {
                    "snapshotIndex": 0,
                    "player": "Alice",
                    "type": "blunder",
                    "severity": "minor",
                    "description": "Bad play",
                    "actionTaken": "Pass",
                    "betterLine": "Cast a threat",
                }
            ],
        )
        errors = list(validator.iter_errors(v8))
        assert errors, "v8 schema should reject annotations without decisionIndex"

    def test_schema_rejects_missing_required_field(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(5))
        bad = {"version": 5}
        errors = list(validator.iter_errors(bad))
        assert len(errors) > 0

    def test_schema_backed_typed_dicts_match_v8_schema(self) -> None:
        schema = _load_schema(8)
        defs = schema["$defs"]

        _assert_typed_dict_matches_schema(GameExport, schema=schema)
        _assert_typed_dict_matches_schema(
            BuiltGameExport,
            schema=schema,
            required_override=set(schema["required"]) - {"annotations", "blunderScriptVersion"},
        )
        _assert_dataclass_matches_schema(Player, schema=defs["Player"])
        _assert_dataclass_matches_schema(
            PilotPlayer,
            schema=defs["Player"],
            required_override=(set(defs["Player"].get("required", [])) | {"model"}) - {"type"},
        )
        _assert_typed_dict_matches_schema(Snapshot, schema=defs["Snapshot"])
        _assert_typed_dict_matches_schema(SnapshotPlayer, schema=defs["SnapshotPlayer"])
        _assert_typed_dict_matches_schema(CombatGroup, schema=defs["CombatGroup"])
        _assert_dataclass_matches_schema(Action, schema=defs["Action"], field_renames={"from_": "from"})
        # LlmEvent is a Union of discriminated variants — verify the union
        # of all variant keys matches the flat JSON schema properties, and the
        # intersection of required keys matches the schema's required set.
        llm_variants = [
            GameStartEvent,
            LlmResponseEvent,
            ToolCallEvent,
            StallEvent,
            ContextResetEvent,
            ContextTrimEvent,
            LlmErrorEvent,
            AutoPilotModeEvent,
        ]
        all_keys: set[str] = set()
        all_required: set[str] | None = None
        for variant in llm_variants:
            all_keys |= _dataclass_keys(variant)
            if all_required is None:
                all_required = _dataclass_required_keys(variant)
            else:
                all_required &= _dataclass_required_keys(variant)
        llm_schema = defs["LlmEvent"]
        assert all_keys == set(llm_schema["properties"]), (
            f"LlmEvent variant keys mismatch: "
            f"extra={all_keys - set(llm_schema['properties'])}, "
            f"missing={set(llm_schema['properties']) - all_keys}"
        )
        assert all_required == set(llm_schema.get("required", [])), (
            f"LlmEvent required keys mismatch: got {all_required}, expected {set(llm_schema.get('required', []))}"
        )
        _assert_dataclass_matches_schema(LlmUsage, schema=defs["LlmUsage"])
        _assert_dataclass_matches_schema(GameOver, schema=defs["GameOver"])
        _assert_dataclass_matches_schema(Annotation, schema=defs["Annotation"])
        _assert_dataclass_matches_schema(Decision, schema=defs["Decision"], extra_fields={"actionSeq"})
        _assert_typed_dict_matches_schema(PilotContext, schema=defs["PilotContext"])
        _assert_dataclass_matches_schema(GameError, schema=defs["GameError"])
        _assert_dataclass_matches_schema(CardMetadata, schema=defs["CardMetadata"])
        _assert_typed_dict_matches_schema(Permanent, schema=defs["Permanent"])
        _assert_typed_dict_matches_schema(StackItem, schema=defs["StackItem"])
        _assert_typed_dict_matches_schema(StackTarget, schema=defs["StackTarget"])
        _assert_typed_dict_matches_schema(CombatCreature, schema=defs["CombatCreature"])
        _assert_typed_dict_matches_schema(Choice, schema=defs["Choice"])
        _assert_typed_dict_matches_schema(MultiAmountItem, schema=defs["MultiAmountItem"])

    def test_typed_loader_accepts_minimal_v8_export(self, tmp_path: Path) -> None:
        path = tmp_path / "game_v8.json"
        payload = _minimal_export(
            8,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model",
                    "toolCallsOk": 3,
                    "toolCallsFailed": 1,
                    "thinkingTimeSecs": 12.5,
                }
            ],
        )
        path.write_text(json.dumps(payload))

        game = load_game_export(path)

        assert game["version"] == 8
        assert game["players"][0].toolCallsOk == 3
        assert game["annotations"] == []

    def test_typed_loader_accepts_gzipped_exports(self, tmp_path: Path) -> None:
        path = tmp_path / "game_v8.json.gz"
        payload = _minimal_export(
            8,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model",
                    "toolCallsOk": 0,
                    "toolCallsFailed": 0,
                    "thinkingTimeSecs": 0.0,
                }
            ],
        )
        path.write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))

        game = load_game_export(path)

        assert game["id"] == "test_v8"

    def test_typed_loader_rejects_unannotated_export(self, tmp_path: Path) -> None:
        path = tmp_path / "game_v8.json"
        payload = _minimal_export(8, season=1, tournament=None)
        del payload["annotations"]
        path.write_text(json.dumps(payload))

        with pytest.raises(AssertionError, match="annotations"):
            load_game_export(path)

    def test_built_export_validator_allows_missing_annotation_fields(self) -> None:
        payload = _minimal_export(
            8,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model",
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
        path = tmp_path / "built_v8.json"
        payload = _minimal_export(
            8,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model",
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

        assert built["version"] == 8
        assert "annotations" not in built

    def test_loader_accepts_empty_decision_strings_allowed_by_schema(self, tmp_path: Path) -> None:
        path = tmp_path / "empty_decision_strings.json"
        payload = _minimal_export(
            8,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model",
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

        assert isinstance(game["decisions"][0], Decision)
        assert game["decisions"][0].actionType == ""
        assert game["decisions"][0].responseType == ""
        assert game["decisions"][0].message == ""

    def test_v8_schema_rejects_pilot_without_model(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(8))
        v8 = _minimal_export(
            8,
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
        errors = list(validator.iter_errors(v8))
        assert errors, "v8 schema should reject pilot player without model"

    def test_v8_schema_accepts_cpu_without_model(self) -> None:
        validator = jsonschema.Draft7Validator(_load_schema(8))
        v8 = _minimal_export(
            8,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Bot",
                    "type": "cpu",
                    "toolCallsOk": 0,
                    "toolCallsFailed": 0,
                    "thinkingTimeSecs": 0.0,
                }
            ],
        )
        errors = list(validator.iter_errors(v8))
        assert errors == [], f"v8 schema should accept cpu player without model: {errors}"

    def test_is_pilot_player_narrows_pilot(self) -> None:
        player = Player(
            name="Alice",
            type="pilot",
            model="test/model",
            toolCallsOk=0,
            toolCallsFailed=0,
            thinkingTimeSecs=0.0,
        )
        assert is_pilot_player(player)

    def test_is_pilot_player_rejects_cpu(self) -> None:
        player = Player(
            name="Bot",
            type="cpu",
            toolCallsOk=0,
            toolCallsFailed=0,
            thinkingTimeSecs=0.0,
        )
        assert not is_pilot_player(player)

    def test_is_pilot_player_crashes_on_pilot_without_model(self) -> None:
        player = Player(
            name="Alice",
            type="pilot",
            toolCallsOk=0,
            toolCallsFailed=0,
            thinkingTimeSecs=0.0,
        )
        with pytest.raises(AssertionError, match="pilot player missing model"):
            is_pilot_player(player)

    def test_validator_rejects_pilot_without_model(self) -> None:
        player = {
            "name": "Alice",
            "type": "pilot",
            "toolCallsOk": 0,
            "toolCallsFailed": 0,
            "thinkingTimeSecs": 0.0,
        }
        with pytest.raises(AssertionError, match="model"):
            _validate_player(player, "test")
