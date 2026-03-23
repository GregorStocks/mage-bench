"""Validate the current game-export JSON schema (structure, rejects bad input).

Full per-game validation is in tests/weird/test_convention_exports.py::TestAllExportsValid.
"""

import copy
import gzip
import json
from dataclasses import MISSING, fields, is_dataclass
from pathlib import Path

import fastjsonschema
import pytest

from magebench.game.game_export_types import (
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
    export_record_field,
    game_export_to_jsonable,
    is_game_export,
    is_pilot_player,
    parse_built_game_export,
    parse_game_export,
    require_built_game_export,
)
from schemas.game_export_migrations import (
    CURRENT_GAME_EXPORT_VERSION,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CURRENT_VERSION = CURRENT_GAME_EXPORT_VERSION
SCHEMA_DIR = REPO_ROOT / "src" / "magebench" / "game"
SCHEMA_DIR = REPO_ROOT / "src" / "magebench" / "game"


def _load_schema(version: int) -> dict:
    path = SCHEMA_DIR / f"game-export-v{version}.schema.json"
    return json.loads(path.read_text())


def _parse_export_path(path: Path) -> GameExport:
    raw = gzip.decompress(path.read_bytes()).decode("utf-8") if path.suffix == ".gz" else path.read_text()
    return parse_game_export(raw, source=path.name)


def _parse_built_export_path(path: Path) -> BuiltGameExport:
    raw = gzip.decompress(path.read_bytes()).decode("utf-8") if path.suffix == ".gz" else path.read_text()
    return parse_built_game_export(raw, source=path.name)


def _minimal_export(version: int, **overrides) -> dict:
    """Build a minimal export payload for schema validation tests."""
    base = {
        "version": version,
        "id": f"test_v{version}",
        "timestamp": "",
        "game_type": "Two Player Duel",
        "deck_type": "Constructed - Standard",
        "total_turns": 0,
        "winner": None,
        "harness_epoch": 0,
        "youtube_url": "",
        "players": [],
        "card_images": {},
        "snapshots": [],
        "actions": [],
        "llm_events": [],
        "game_over": None,
        "annotations": [],
        "blunder_script_version": 0,
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
    return {
        renamed.get(field.name, field.metadata.get("json_key", field.name))
        for field in fields(cls)
        if not field.name.startswith("_") and field.name not in ignored
    }


def _dataclass_required_keys(
    cls: type,
    *,
    ignored_fields: set[str] | None = None,
    renames: dict[str, str] | None = None,
) -> set[str]:
    ignored = ignored_fields or set()
    renamed = renames or {}
    return {
        renamed.get(field.name, field.metadata.get("json_key", field.name))
        for field in fields(cls)
        if not field.name.startswith("_")
        and field.name not in ignored
        and field.default is MISSING
        and field.default_factory is MISSING
    }


def _assert_dataclass_matches_schema(
    dataclass_cls: type[object],
    *,
    schema: dict,
    required_override: set[str] | None = None,
    extra_fields: set[str] | None = None,
    field_renames: dict[str, str] | None = None,
) -> None:
    assert is_dataclass(dataclass_cls)
    expected_props = set(schema["properties"])
    expected_required = required_override if required_override is not None else set(schema.get("required", []))
    actual_fields = fields(dataclass_cls)
    internal_fields = [field for field in actual_fields if field.name.startswith("_")]
    ignored_fields = {field.name for field in internal_fields}
    if extra_fields:
        ignored_fields |= extra_fields
    assert (
        _dataclass_keys(
            dataclass_cls,
            ignored_fields=ignored_fields,
            renames=field_renames,
        )
        == expected_props
    )
    assert all(field.default is not MISSING or field.default_factory is not MISSING for field in internal_fields), (
        "internal dataclass fields must be optional"
    )
    assert (
        _dataclass_required_keys(
            dataclass_cls,
            ignored_fields=ignored_fields,
            renames=field_renames,
        )
        == expected_required
    )


class TestExportSchema:
    def test_v9_schema_is_valid(self) -> None:
        schema = _load_schema(CURRENT_VERSION)
        fastjsonschema.compile(schema)

    def test_v9_schema_accepts_v9(self) -> None:
        validate = fastjsonschema.compile(_load_schema(CURRENT_VERSION))
        payload = _minimal_export(
            CURRENT_VERSION,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model",
                    "tool_calls_ok": 3,
                    "tool_calls_failed": 1,
                    "thinking_time_secs": 12.5,
                }
            ],
            decisions=[
                {
                    "index": 0,
                    "snapshot_index": 0,
                    "player": "Alice",
                    "turn": 1,
                    "phase": None,
                    "action_type": "",
                    "response_type": "",
                    "message": "",
                    "choices": [],
                    "choice_count": 0,
                    "is_forced": True,
                    "llm_event_indices": [],
                    "subsequent_actions": [],
                }
            ],
            annotations=[
                {
                    "decision_index": 0,
                    "snapshot_index": 0,
                    "player": "Alice",
                    "type": "blunder",
                    "severity": "minor",
                    "description": "Bad play",
                    "action_taken": "Pass",
                    "better_line": "Cast a threat",
                }
            ],
        )
        validate(payload)

    def test_v9_schema_rejects_other_version_numbers(self) -> None:
        validate = fastjsonschema.compile(_load_schema(CURRENT_VERSION))
        with pytest.raises(fastjsonschema.JsonSchemaValueException):
            validate(_minimal_export(8, season=1, tournament=None))

    def test_v9_schema_requires_annotation_decision_index(self) -> None:
        validate = fastjsonschema.compile(_load_schema(CURRENT_VERSION))
        payload = _minimal_export(
            CURRENT_VERSION,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model",
                    "tool_calls_ok": 3,
                    "tool_calls_failed": 1,
                    "thinking_time_secs": 12.5,
                }
            ],
            annotations=[
                {
                    "snapshot_index": 0,
                    "player": "Alice",
                    "type": "blunder",
                    "severity": "minor",
                    "description": "Bad play",
                    "action_taken": "Pass",
                    "better_line": "Cast a threat",
                }
            ],
        )
        with pytest.raises(fastjsonschema.JsonSchemaValueException):
            validate(payload)

    def test_schema_rejects_missing_required_field(self) -> None:
        validate = fastjsonschema.compile(_load_schema(CURRENT_VERSION))
        with pytest.raises(fastjsonschema.JsonSchemaValueException):
            validate({"version": CURRENT_VERSION})

    def test_schema_backed_typed_dicts_match_v9_schema(self) -> None:
        schema = _load_schema(CURRENT_VERSION)
        defs = schema["$defs"]

        _assert_dataclass_matches_schema(GameExport, schema=schema)
        _assert_dataclass_matches_schema(
            BuiltGameExport,
            schema=schema,
            required_override=set(schema["required"]) - {"annotations", "blunder_script_version"},
        )
        _assert_dataclass_matches_schema(Player, schema=defs["Player"])
        _assert_dataclass_matches_schema(
            PilotPlayer,
            schema=defs["Player"],
            required_override=(set(defs["Player"].get("required", [])) | {"model"}) - {"type"},
        )
        _assert_dataclass_matches_schema(Snapshot, schema=defs["Snapshot"])
        _assert_dataclass_matches_schema(SnapshotPlayer, schema=defs["SnapshotPlayer"])
        _assert_dataclass_matches_schema(CombatGroup, schema=defs["CombatGroup"])
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
        _assert_dataclass_matches_schema(Decision, schema=defs["Decision"], extra_fields={"action_seq"})
        _assert_dataclass_matches_schema(GameError, schema=defs["GameError"])
        _assert_dataclass_matches_schema(CardMetadata, schema=defs["CardMetadata"])
        _assert_dataclass_matches_schema(PilotContext, schema=defs["PilotContext"])
        _assert_dataclass_matches_schema(Permanent, schema=defs["Permanent"])
        _assert_dataclass_matches_schema(StackItem, schema=defs["StackItem"])
        _assert_dataclass_matches_schema(StackTarget, schema=defs["StackTarget"])
        _assert_dataclass_matches_schema(CombatCreature, schema=defs["CombatCreature"])
        _assert_dataclass_matches_schema(Choice, schema=defs["Choice"])
        _assert_dataclass_matches_schema(MultiAmountItem, schema=defs["MultiAmountItem"])

    def test_typed_loader_accepts_minimal_v9_export(self, tmp_path: Path) -> None:
        path = tmp_path / "game_v9.json5"
        payload = _minimal_export(
            CURRENT_VERSION,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model",
                    "tool_calls_ok": 3,
                    "tool_calls_failed": 1,
                    "thinking_time_secs": 12.5,
                }
            ],
        )
        path.write_text(json.dumps(payload))

        game = _parse_export_path(path)

        assert game.version == CURRENT_VERSION
        assert game.players[0].tool_calls_ok == 3
        assert game.annotations == []

    def test_typed_loader_rejects_legacy_v8_export(self, tmp_path: Path) -> None:
        path = tmp_path / "game_v8.json5"
        payload = _minimal_export(
            8,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model",
                    "tool_calls_ok": 3,
                    "tool_calls_failed": 1,
                    "thinking_time_secs": 12.5,
                }
            ],
        )
        path.write_text(json.dumps(payload))

        with pytest.raises(AssertionError, match="Unsupported game export version 8; expected 9"):
            _parse_export_path(path)

    def test_typed_loader_accepts_gzipped_exports(self, tmp_path: Path) -> None:
        path = tmp_path / "game_v9.json5.gz"
        payload = _minimal_export(
            CURRENT_VERSION,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model",
                    "tool_calls_ok": 0,
                    "tool_calls_failed": 0,
                    "thinking_time_secs": 0.0,
                }
            ],
        )
        path.write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))

        game = _parse_export_path(path)

        assert game.id == f"test_v{CURRENT_VERSION}"

    def test_loader_coerces_board_and_stack_leaf_records_to_dataclasses(self, tmp_path: Path) -> None:
        path = tmp_path / "game_v9.json5"
        payload = _minimal_export(
            CURRENT_VERSION,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model",
                    "tool_calls_ok": 0,
                    "tool_calls_failed": 0,
                    "thinking_time_secs": 0.0,
                }
            ],
            snapshots=[
                {
                    "seq": 1,
                    "turn": 1,
                    "phase": "PRECOMBAT_MAIN",
                    "step": None,
                    "active_player": "Alice",
                    "priority_player": "Alice",
                    "players": [
                        {
                            "name": "Alice",
                            "life": 20,
                            "library_size": 53,
                            "battlefield": [
                                {
                                    "name": "Llanowar Elves",
                                    "id": "p1",
                                    "summoning_sick": True,
                                    "visible_to": ["Alice"],
                                    "rules": "Tap: Add {G}.",
                                }
                            ],
                            "graveyard": [],
                            "hand": [
                                {
                                    "name": "Lightning Bolt",
                                    "id": "h1",
                                    "mana_cost": "{R}",
                                    "type_line": "Instant",
                                }
                            ],
                        }
                    ],
                    "stack": [
                        {
                            "name": "Lightning Bolt",
                            "controller": "Alice",
                            "targets": [{"name": "Llanowar Elves", "id": "p1"}],
                        }
                    ],
                    "combat": [
                        {
                            "attackers": [{"name": "Goblin Guide", "id": "a1"}],
                            "blockers": [{"name": "Wall of Omens", "id": "b1"}],
                            "blocked": True,
                            "defending": "Bob",
                        }
                    ],
                }
            ],
            decisions=[
                {
                    "index": 0,
                    "snapshot_index": 0,
                    "player": "Alice",
                    "turn": 1,
                    "phase": "PRECOMBAT_MAIN",
                    "action_type": "cast",
                    "response_type": "select",
                    "message": "Play spells and abilities",
                    "choices": [],
                    "choice_count": 0,
                    "is_forced": True,
                    "llm_event_indices": [],
                    "subsequent_actions": [],
                    "pilot_context": {"incoming_attackers": [{"name": "Goblin Guide", "id": "a1"}]},
                }
            ],
        )
        path.write_text(json.dumps(payload))

        game = _parse_export_path(path)

        snap = game.snapshots[0]
        battlefield_card = snap.players[0].battlefield[0]
        stack_item = snap.stack[0]
        target = stack_item.targets[0] if isinstance(stack_item, StackItem) and stack_item.targets else None
        assert snap.combat is not None
        attacker = snap.combat[0].attackers[0]
        assert game.decisions is not None
        pilot_ctx = game.decisions[0]["pilot_context"]
        assert isinstance(pilot_ctx, PilotContext)
        incoming_list = pilot_ctx.get_value("incoming_attackers")
        assert isinstance(incoming_list, list)
        incoming = incoming_list[0]

        assert isinstance(battlefield_card, Permanent)
        assert isinstance(stack_item, StackItem)
        assert isinstance(target, StackTarget)
        assert isinstance(attacker, CombatCreature)
        assert isinstance(incoming, CombatCreature)
        assert export_record_field(battlefield_card, "visible_to") == ["Alice"]
        assert export_record_field(battlefield_card, "rules") == "Tap: Add {G}."
        assert export_record_field(snap.players[0].hand[0], "mana_cost") == "{R}"
        assert export_record_field(snap.players[0].hand[0], "type_line") == "Instant"
        assert export_record_field(stack_item, "controller") == "Alice"

    def test_is_game_export_accepts_already_coerced_leaf_dataclasses(self, tmp_path: Path) -> None:
        path = tmp_path / "game_v9.json5"
        payload = _minimal_export(
            CURRENT_VERSION,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model",
                    "tool_calls_ok": 0,
                    "tool_calls_failed": 0,
                    "thinking_time_secs": 0.0,
                }
            ],
            snapshots=[
                {
                    "seq": 1,
                    "turn": 1,
                    "phase": "PRECOMBAT_MAIN",
                    "step": None,
                    "active_player": "Alice",
                    "priority_player": "Alice",
                    "players": [
                        {
                            "name": "Alice",
                            "life": 20,
                            "library_size": 53,
                            "battlefield": [{"name": "Llanowar Elves", "id": "p1"}],
                            "graveyard": [],
                            "hand": [],
                        }
                    ],
                    "stack": [
                        {
                            "name": "Lightning Bolt",
                            "targets": [{"name": "", "id": "p1"}],
                        }
                    ],
                    "combat": [],
                }
            ],
        )
        path.write_text(json.dumps(payload))

        game = _parse_export_path(path)

        snap = game.snapshots[0]
        assert isinstance(snap.players[0].battlefield[0], Permanent)
        assert isinstance(snap.stack[0], StackItem)
        assert is_game_export(game)

    def test_loader_accepts_schema_valid_leaf_extras_key(self, tmp_path: Path) -> None:
        path = tmp_path / "game_v9.json5"
        payload = _minimal_export(
            CURRENT_VERSION,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model",
                    "tool_calls_ok": 0,
                    "tool_calls_failed": 0,
                    "thinking_time_secs": 0.0,
                }
            ],
            snapshots=[
                {
                    "seq": 1,
                    "turn": 1,
                    "phase": "PRECOMBAT_MAIN",
                    "step": None,
                    "active_player": "Alice",
                    "priority_player": "Alice",
                    "players": [
                        {
                            "name": "Alice",
                            "life": 20,
                            "library_size": 53,
                            "battlefield": [
                                {
                                    "name": "Llanowar Elves",
                                    "_extras": {"nested": True},
                                    "rules": "Tap: Add {G}.",
                                }
                            ],
                            "graveyard": [],
                            "hand": [],
                        }
                    ],
                    "stack": [],
                    "combat": [],
                }
            ],
        )
        path.write_text(json.dumps(payload))

        game = _parse_export_path(path)

        snap = game.snapshots[0]
        battlefield_card = snap.players[0].battlefield[0]
        assert isinstance(battlefield_card, Permanent)
        assert battlefield_card._extras["_extras"] == {"nested": True}
        assert export_record_field(battlefield_card, "rules") == "Tap: Add {G}."

    def test_typed_loader_rejects_unannotated_export(self, tmp_path: Path) -> None:
        path = tmp_path / "game_v9.json5"
        payload = _minimal_export(CURRENT_VERSION, season=1, tournament=None)
        del payload["annotations"]
        path.write_text(json.dumps(payload))

        with pytest.raises(AssertionError, match="annotations"):
            _parse_export_path(path)

    def test_built_export_validator_allows_missing_annotation_fields(self) -> None:
        payload = _minimal_export(
            CURRENT_VERSION,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model",
                    "tool_calls_ok": 1,
                    "tool_calls_failed": 0,
                    "thinking_time_secs": 2.0,
                }
            ],
        )
        del payload["annotations"]
        del payload["blunder_script_version"]

        built = require_built_game_export(payload, source="built export")

        assert built.season == 1
        assert built.annotations is None

    def test_built_loader_accepts_unannotated_export(self, tmp_path: Path) -> None:
        path = tmp_path / "built_v9.json5"
        payload = _minimal_export(
            CURRENT_VERSION,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model",
                    "tool_calls_ok": 1,
                    "tool_calls_failed": 0,
                    "thinking_time_secs": 2.0,
                }
            ],
        )
        del payload["annotations"]
        del payload["blunder_script_version"]
        path.write_text(json.dumps(payload))

        built = _parse_built_export_path(path)

        assert built.version == CURRENT_VERSION
        assert built.annotations is None

    def test_loader_accepts_empty_decision_strings_allowed_by_schema(self, tmp_path: Path) -> None:
        path = tmp_path / "empty_decision_strings.json5"
        payload = _minimal_export(
            CURRENT_VERSION,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "test/model",
                    "tool_calls_ok": 0,
                    "tool_calls_failed": 0,
                    "thinking_time_secs": 0.0,
                }
            ],
            decisions=[
                {
                    "index": 0,
                    "snapshot_index": 0,
                    "player": "Alice",
                    "turn": 1,
                    "phase": None,
                    "action_type": "",
                    "response_type": "",
                    "message": "",
                    "choices": [],
                    "choice_count": 0,
                    "is_forced": True,
                    "llm_event_indices": [],
                    "subsequent_actions": [],
                }
            ],
        )
        path.write_text(json.dumps(payload))

        game = _parse_export_path(path)

        assert game.decisions is not None
        assert isinstance(game.decisions[0], Decision)
        assert game.decisions[0].action_type == ""
        assert game.decisions[0].response_type == ""
        assert game.decisions[0].message == ""

    def test_loader_coerces_decision_support_records_to_dataclasses(self, tmp_path: Path) -> None:
        path = tmp_path / "decision_support.json5"
        payload = _minimal_export(
            CURRENT_VERSION,
            season=1,
            tournament=None,
            decisions=[
                {
                    "index": 0,
                    "snapshot_index": 0,
                    "player": "Alice",
                    "turn": 1,
                    "phase": "PRECOMBAT_MAIN",
                    "action_type": "play",
                    "response_type": "choice",
                    "message": "Play spells and abilities",
                    "choices": [
                        {
                            "index": 0,
                            "name": "Memnite",
                            "id": "p1",
                            "action": "cast",
                            "mana_cost": "{0}",
                            "power": "1",
                            "toughness": "1",
                        }
                    ],
                    "choice_count": 1,
                    "is_forced": True,
                    "llm_event_indices": [],
                    "subsequent_actions": [],
                    "pilot_context": {
                        "untapped_lands": 1,
                        "land_drops_used": 0,
                        "combat_phase": None,
                        "manaPool": {"WHITE": 1},
                    },
                    "items": [
                        {
                            "description": "Assign damage to Memnite",
                            "min": 0,
                            "max": 1,
                            "target": "p1",
                        }
                    ],
                    "total_min": 0,
                    "total_max": 1,
                }
            ],
        )
        path.write_text(json.dumps(payload))

        game = _parse_export_path(path)
        assert game.decisions is not None
        decision = game.decisions[0]
        choice = decision["choices"][0]
        pilot_context = decision["pilot_context"]
        item = decision["items"][0]

        assert isinstance(choice, Choice)
        assert not isinstance(choice, dict)
        assert choice.name == "Memnite"
        assert choice.extras["power"] == "1"
        assert isinstance(pilot_context, PilotContext)
        assert pilot_context.land_drops_used == 0
        assert pilot_context.has_field("combat_phase")
        assert pilot_context.combat_phase is None
        assert pilot_context.extras["manaPool"] == {"WHITE": 1}
        assert isinstance(item, MultiAmountItem)
        assert item.description == "Assign damage to Memnite"
        assert item.extras["target"] == "p1"

    def test_game_export_to_jsonable_serializes_export_dataclasses(self) -> None:
        payload = _minimal_export(
            CURRENT_VERSION,
            season=1,
            tournament=None,
            llm_events=[
                {
                    "type": "game_start",
                    "player": "Alice",
                    "model": "test-model",
                    "available_tools": ["pass_priority"],
                }
            ],
            decisions=[
                {
                    "index": 0,
                    "snapshot_index": 0,
                    "player": "Alice",
                    "turn": 1,
                    "phase": "PRECOMBAT_MAIN",
                    "action_type": "play",
                    "response_type": "choice",
                    "message": "Play spells and abilities",
                    "choices": [{"index": 0, "name": "Memnite"}],
                    "choice_count": 1,
                    "is_forced": True,
                    "llm_event_indices": [],
                    "subsequent_actions": [],
                    "pilot_context": {"untapped_lands": 1, "manaPool": {"WHITE": 1}},
                    "items": [{"description": "Assign damage"}],
                }
            ],
        )

        built = require_built_game_export(payload, source="built export")
        cloned = copy.deepcopy(built)
        json_ready = game_export_to_jsonable(built)

        assert isinstance(built.llm_events[0], GameStartEvent)
        assert built.decisions is not None
        assert isinstance(built.decisions[0]["choices"][0], Choice)
        assert cloned.decisions is not None
        assert isinstance(cloned.decisions[0]["choices"][0], Choice)
        json_round_trip = json.loads(json.dumps(json_ready))
        assert json_round_trip["llm_events"][0] == {
            "type": "game_start",
            "player": "Alice",
            "model": "test-model",
            "available_tools": ["pass_priority"],
        }
        assert json_round_trip["decisions"][0]["pilot_context"] == {
            "untapped_lands": 1,
            "manaPool": {"WHITE": 1},
        }

    def test_validator_rejects_invalid_prebuilt_choice_instance(self) -> None:
        payload = _minimal_export(
            CURRENT_VERSION,
            season=1,
            tournament=None,
            decisions=[
                {
                    "index": 0,
                    "snapshot_index": 0,
                    "player": "Alice",
                    "turn": 1,
                    "phase": "PRECOMBAT_MAIN",
                    "action_type": "play",
                    "response_type": "choice",
                    "message": "Play spells and abilities",
                    "choices": [Choice(index="oops")],  # type: ignore[arg-type]
                    "choice_count": 1,
                    "is_forced": True,
                    "llm_event_indices": [],
                    "subsequent_actions": [],
                }
            ],
        )

        with pytest.raises(AssertionError, match=r"(choices\[0\]\.index|Choice\.index)"):
            require_built_game_export(payload, source="built export")

    def test_decision_support_dataclass_extras_are_read_only(self) -> None:
        choice = Choice.from_mapping({"name": "Memnite", "power": "1"})

        with pytest.raises(TypeError):
            choice.extras["power"] = "2"  # type: ignore[index]

    def test_decision_support_dataclass_equality_includes_extra_keys(self) -> None:
        assert Choice.from_mapping({"name": "Memnite", "power": "1"}) != Choice.from_mapping(
            {"name": "Memnite", "power": "2"}
        )
        assert PilotContext.from_mapping({"untapped_lands": 1, "manaPool": {"WHITE": 1}}) != PilotContext.from_mapping(
            {"untapped_lands": 1, "manaPool": {"BLUE": 1}}
        )
        assert PilotContext.from_mapping({"combat_phase": None}) != PilotContext()

    def test_v9_schema_rejects_pilot_without_model(self) -> None:
        validate = fastjsonschema.compile(_load_schema(CURRENT_VERSION))
        payload = _minimal_export(
            CURRENT_VERSION,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Alice",
                    "type": "pilot",
                    "tool_calls_ok": 0,
                    "tool_calls_failed": 0,
                    "thinking_time_secs": 0.0,
                }
            ],
        )
        with pytest.raises(fastjsonschema.JsonSchemaValueException):
            validate(payload)

    def test_v9_schema_accepts_cpu_without_model(self) -> None:
        validate = fastjsonschema.compile(_load_schema(CURRENT_VERSION))
        payload = _minimal_export(
            CURRENT_VERSION,
            season=1,
            tournament=None,
            players=[
                {
                    "name": "Bot",
                    "type": "cpu",
                    "tool_calls_ok": 0,
                    "tool_calls_failed": 0,
                    "thinking_time_secs": 0.0,
                }
            ],
        )
        validate(payload)

    def test_is_pilot_player_narrows_pilot(self) -> None:
        player = Player(
            name="Alice",
            type="pilot",
            model="test/model",
            tool_calls_ok=0,
            tool_calls_failed=0,
            thinking_time_secs=0.0,
        )
        assert is_pilot_player(player)

    def test_is_pilot_player_rejects_cpu(self) -> None:
        player = Player(
            name="Bot",
            type="cpu",
            tool_calls_ok=0,
            tool_calls_failed=0,
            thinking_time_secs=0.0,
        )
        assert not is_pilot_player(player)

    def test_is_pilot_player_crashes_on_pilot_without_model(self) -> None:
        player = Player(
            name="Alice",
            type="pilot",
            tool_calls_ok=0,
            tool_calls_failed=0,
            thinking_time_secs=0.0,
        )
        with pytest.raises(AssertionError, match="pilot player missing model"):
            is_pilot_player(player)

    def test_validator_rejects_pilot_without_model(self) -> None:
        player = {
            "name": "Alice",
            "type": "pilot",
            "tool_calls_ok": 0,
            "tool_calls_failed": 0,
            "thinking_time_secs": 0.0,
        }
        with pytest.raises(AssertionError, match="model"):
            _validate_player(player, "test")
