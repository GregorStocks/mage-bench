"""Test migration round-trip fidelity and runner path-finding."""

import gzip
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from puppeteer.harness_epoch import SEASON_1_START_EPOCH
from schemas.game_export_types import Action, Choice, MultiAmountItem, PilotContext
from schemas.migrations import (
    MIGRATIONS,
    v2_to_v3,
    v3_to_v4,
    v4_to_v5,
    v5_to_v6,
    v6_to_v7,
    v7_to_v8,
)
from scripts.backfill_decisions import backfill_game
from scripts.export_game import _build_decisions, _collect_card_names, _trim_card
from scripts.game_exports import (
    glob_game_export_paths,
    load_raw_game_export,
    write_raw_game_export,
)
from scripts.json5_utils import loads_json5
from scripts.migrate_exports import find_migration_path


def _make_v2_export() -> dict:
    """Create a minimal v2 export with realistic card data."""
    return {
        "version": 2,
        "id": "game_20260301_120000",
        "timestamp": "2026-03-01T12:00:00-08:00",
        "gameType": "Two Player Duel",
        "deckType": "Constructed - Standard",
        "totalTurns": 5,
        "winner": "Alice",
        "harnessEpoch": 40,
        "youtubeUrl": "",
        "players": [
            {"name": "Alice", "type": "pilot"},
            {"name": "Bob", "type": "cpu"},
        ],
        "cardImages": {
            "Lightning Bolt": "https://api.scryfall.com/cards/lea/161?format=image&version=small",
            "Mountain": "https://api.scryfall.com/cards/lea/292?format=image&version=small",
        },
        "snapshots": [
            {
                "seq": 1,
                "turn": 1,
                "phase": "MAIN",
                "step": "MAIN",
                "active_player": "Alice",
                "priority_player": "Alice",
                "players": [
                    {
                        "name": "Alice",
                        "life": 20,
                        "library_size": 53,
                        "hand": [{"name": "Lightning Bolt"}],
                        "battlefield": [{"name": "Mountain"}],
                        "graveyard": [],
                        "exile": [],
                    },
                    {
                        "name": "Bob",
                        "life": 20,
                        "library_size": 53,
                        "hand": [],
                        "battlefield": [
                            {"name": "Goblin Token"},
                        ],
                        "graveyard": [],
                        "exile": [],
                    },
                ],
                "stack": [],
            }
        ],
        "actions": [],
        "llmEvents": [],
        "llmTrace": [],
        "gameOver": {"seq": 100, "message": "Alice wins"},
        "annotations": [],
        "blunderScriptVersion": 1,
    }


def _make_v3_export(*, harness_epoch: int = 40) -> dict:
    """Create a minimal v3 export with cardData."""
    return {
        "version": 3,
        "id": "game_20260301_120000",
        "timestamp": "2026-03-01T12:00:00-08:00",
        "gameType": "Two Player Duel",
        "deckType": "Constructed - Standard",
        "totalTurns": 5,
        "winner": "Alice",
        "harnessEpoch": harness_epoch,
        "youtubeUrl": "",
        "players": [
            {"name": "Alice", "type": "pilot"},
            {"name": "Bob", "type": "cpu"},
        ],
        "cardImages": {
            "Lightning Bolt": "https://api.scryfall.com/cards/lea/161?format=image&version=small",
            "Mountain": "https://api.scryfall.com/cards/lea/292?format=image&version=small",
            "Goblin Token": "https://cards.scryfall.io/small/front/token/goblin.jpg",
        },
        "cardData": {
            "Lightning Bolt": {
                "mana_cost": "{R}",
                "type_line": "Instant",
                "oracle_text": "Lightning Bolt deals 3 damage to any target.",
            },
            "Mountain": {
                "type_line": "Basic Land — Mountain",
            },
        },
        "snapshots": [],
        "actions": [],
        "llmEvents": [],
        "llmTrace": [],
        "gameOver": {"seq": 100, "message": "Alice wins"},
        "annotations": [],
        "blunderScriptVersion": 1,
    }


# Mock Scryfall responses
_MOCK_SCRYFALL_DATA = {
    "Lightning Bolt": {
        "name": "Lightning Bolt",
        "mana_cost": "{R}",
        "type_line": "Instant",
        "oracle_text": "Lightning Bolt deals 3 damage to any target.",
    },
    "Mountain": {
        "name": "Mountain",
        "type_line": "Basic Land — Mountain",
    },
}


def _mock_collection(names):
    found = []
    not_found = []
    for name in names:
        if name in _MOCK_SCRYFALL_DATA:
            found.append(_MOCK_SCRYFALL_DATA[name])
        else:
            not_found.append({"name": name})
    return found, not_found


def _mock_named(name):
    return _MOCK_SCRYFALL_DATA.get(name)


def _mock_search_token(token_name):
    if "Goblin" in token_name:
        return "https://cards.scryfall.io/small/front/token/goblin.jpg"
    return None


class TestMigrateV2V3:
    def test_v2_to_v3_up_adds_card_data_and_token_images(self) -> None:
        v2 = _make_v2_export()
        original_card_images = dict(v2["cardImages"])

        with (
            patch("scripts.scryfall.collection", side_effect=_mock_collection),
            patch("scripts.scryfall.named", side_effect=_mock_named),
            patch("scripts.scryfall.search_token", side_effect=_mock_search_token),
        ):
            v3 = v2_to_v3.up(v2)

        assert v3["version"] == 3

        # Token image should be added
        assert "Goblin Token" in v3["cardImages"]
        assert v3["cardImages"]["Goblin Token"].startswith("https://")

        # Original images still present
        for name, url in original_card_images.items():
            assert v3["cardImages"][name] == url

        # Card data should have metadata for real cards
        assert "Lightning Bolt" in v3["cardData"]
        assert v3["cardData"]["Lightning Bolt"]["mana_cost"] == "{R}"
        assert "Mountain" in v3["cardData"]

        # Token should NOT be in card_data (only real cards)
        assert "Goblin Token" not in v3["cardData"]

    def test_v3_to_v2_down_removes_card_data_and_tokens(self) -> None:
        v3 = _make_v3_export()
        v2 = v2_to_v3.down(v3)

        assert v2["version"] == 2
        assert "cardData" not in v2
        assert "Goblin Token" not in v2["cardImages"]
        assert "Lightning Bolt" in v2["cardImages"]
        assert "Mountain" in v2["cardImages"]

    def test_round_trip_preserves_v2_card_images(self) -> None:
        """v2 → v3 → v2 should produce the same cardImages as the original."""
        v2_original = _make_v2_export()
        original_images = dict(v2_original["cardImages"])

        with (
            patch("scripts.scryfall.collection", side_effect=_mock_collection),
            patch("scripts.scryfall.named", side_effect=_mock_named),
            patch("scripts.scryfall.search_token", side_effect=_mock_search_token),
        ):
            v3 = v2_to_v3.up(json.loads(json.dumps(v2_original)))

        v2_restored = v2_to_v3.down(v3)

        assert v2_restored["version"] == 2
        assert v2_restored["cardImages"] == original_images
        assert "cardData" not in v2_restored


class TestMigrateV3V4:
    def test_v3_to_v4_up_adds_season_and_tournament(self) -> None:
        v4 = v3_to_v4.up(_make_v3_export(harness_epoch=40))
        assert v4["version"] == 4
        assert v4["season"] == 1
        assert v4["tournament"] is None

    def test_v3_to_v4_up_pre_season(self) -> None:
        v4 = v3_to_v4.up(_make_v3_export(harness_epoch=5))
        assert v4["season"] == 0

    def test_v3_to_v4_boundary(self) -> None:
        """Epoch exactly at SEASON_1_START_EPOCH should be season 1."""
        assert v3_to_v4.compute_season(SEASON_1_START_EPOCH) == 1
        assert v3_to_v4.compute_season(SEASON_1_START_EPOCH - 1) == 0

    def test_v4_to_v3_down_removes_season_and_tournament(self) -> None:
        v4 = _make_v3_export()
        v4["version"] = 4
        v4["season"] = 1
        v4["tournament"] = None

        v3 = v3_to_v4.down(v4)
        assert v3["version"] == 3
        assert "season" not in v3
        assert "tournament" not in v3

    def test_round_trip_preserves_v3_structure(self) -> None:
        """v3 → v4 → v3 should produce identical JSON."""
        v3_original = _make_v3_export()
        original_json = json.dumps(v3_original, sort_keys=True)

        v4 = v3_to_v4.up(json.loads(original_json))
        v3_restored = v3_to_v4.down(v4)

        assert json.dumps(v3_restored, sort_keys=True) == original_json

    def test_round_trip_preserves_v3_pre_season(self) -> None:
        """Round-trip with a pre-season game."""
        v3_original = _make_v3_export(harness_epoch=5)
        original_json = json.dumps(v3_original, sort_keys=True)

        v4 = v3_to_v4.up(json.loads(original_json))
        assert v4["season"] == 0

        v3_restored = v3_to_v4.down(v4)
        assert json.dumps(v3_restored, sort_keys=True) == original_json


def _make_v4_export() -> dict:
    """Create a minimal v4 export with legacy list-format chosenArgs."""
    v4 = _make_v3_export()
    v4["version"] = 4
    v4["season"] = 1
    v4["tournament"] = None
    v4["decisions"] = [
        {
            "chosenArgs": {
                "mana_plan": ["WHITE", "GREEN"],
                "attackers": ["p1", "p5"],
                "blockers": ["p3:p10", "p7:p12"],
                "choice": "1",
            },
        },
        {
            "chosenArgs": {
                "mana_plan": [],
                "attackers": [],
                "blockers": [],
            },
        },
        {
            "chosenArgs": {},
        },
    ]
    return v4


class TestMigrateV4V5:
    def test_v4_to_v5_up_converts_lists_to_csv(self) -> None:
        v5 = v4_to_v5.up(_make_v4_export())
        assert v5["version"] == 5

        args0 = v5["decisions"][0]["chosenArgs"]
        assert args0["mana_plan"] == "WHITE,GREEN"
        assert args0["attackers"] == "p1,p5"
        assert args0["blockers"] == "p3:p10,p7:p12"
        # Non-CSV fields are untouched
        assert args0["choice"] == "1"

        # Empty lists become empty strings
        args1 = v5["decisions"][1]["chosenArgs"]
        assert args1["mana_plan"] == ""
        assert args1["attackers"] == ""
        assert args1["blockers"] == ""

    def test_v4_to_v5_up_preserves_already_string(self) -> None:
        v4 = _make_v4_export()
        # Simulate a game that already has string format
        v4["decisions"][0]["chosenArgs"]["mana_plan"] = "WHITE,GREEN"
        v5 = v4_to_v5.up(v4)
        assert v5["decisions"][0]["chosenArgs"]["mana_plan"] == "WHITE,GREEN"

    def test_v5_to_v4_down_converts_csv_to_lists(self) -> None:
        v5 = _make_v4_export()
        v5["version"] = 5
        # Set string format as v5 would have
        v5["decisions"][0]["chosenArgs"]["mana_plan"] = "WHITE,GREEN"
        v5["decisions"][0]["chosenArgs"]["attackers"] = "p1,p5"
        v5["decisions"][0]["chosenArgs"]["blockers"] = "p3:p10,p7:p12"
        v5["decisions"][1]["chosenArgs"]["mana_plan"] = ""
        v5["decisions"][1]["chosenArgs"]["attackers"] = ""
        v5["decisions"][1]["chosenArgs"]["blockers"] = ""

        v4 = v4_to_v5.down(v5)
        assert v4["version"] == 4
        assert v4["decisions"][0]["chosenArgs"]["mana_plan"] == ["WHITE", "GREEN"]
        assert v4["decisions"][0]["chosenArgs"]["attackers"] == ["p1", "p5"]
        assert v4["decisions"][1]["chosenArgs"]["mana_plan"] == []

    def test_round_trip_preserves_v4_structure(self) -> None:
        """v4 → v5 → v4 should produce identical JSON."""
        v4_original = _make_v4_export()
        original_json = json.dumps(v4_original, sort_keys=True)

        v5 = v4_to_v5.up(json.loads(original_json))
        v4_restored = v4_to_v5.down(v5)

        assert json.dumps(v4_restored, sort_keys=True) == original_json


class TestExportGameHelpers:
    """Tests for export_game.py helper functions (unchanged by refactor)."""

    def test_trim_card_extracts_correct_fields(self) -> None:
        full_card = {
            "name": "Lightning Bolt",
            "mana_cost": "{R}",
            "type_line": "Instant",
            "oracle_text": "Lightning Bolt deals 3 damage to any target.",
            "cmc": 1.0,
            "colors": ["R"],
            "set": "lea",
            "rarity": "common",
            "image_uris": {"small": "https://..."},
            "power": None,
            "toughness": None,
        }
        trimmed = _trim_card(full_card)
        assert set(trimmed.keys()) == {"mana_cost", "type_line", "oracle_text"}
        assert trimmed["mana_cost"] == "{R}"
        # None values should be excluded
        assert "power" not in trimmed
        assert "toughness" not in trimmed

    def test_trim_card_includes_creature_stats(self) -> None:
        creature = {
            "name": "Grizzly Bears",
            "mana_cost": "{1}{G}",
            "type_line": "Creature — Bear",
            "oracle_text": "",
            "power": "2",
            "toughness": "2",
        }
        trimmed = _trim_card(creature)
        assert trimmed["power"] == "2"
        assert trimmed["toughness"] == "2"

    def test_collect_card_names_separates_tokens_and_cards(self) -> None:
        snapshots = [
            {
                "seq": 1,
                "turn": 1,
                "phase": "MAIN",
                "step": "MAIN",
                "active_player": "A",
                "priority_player": "A",
                "players": [
                    {
                        "name": "A",
                        "life": 20,
                        "library_size": 50,
                        "hand": [{"name": "Lightning Bolt"}],
                        "battlefield": [
                            {"name": "Mountain"},
                            {"name": "Goblin Token"},
                        ],
                        "graveyard": ["Shock"],
                        "exile": [],
                    }
                ],
                "stack": [],
            }
        ]
        real_cards, tokens = _collect_card_names(snapshots)
        assert "Lightning Bolt" in real_cards
        assert "Mountain" in real_cards
        assert "Shock" in real_cards
        assert "Goblin Token" in tokens
        assert "Goblin Token" not in real_cards

    def test_build_decisions_keeps_successful_retry_and_skips_blank_follow_up(self) -> None:
        snapshots = [
            {
                "seq": 10,
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "step": "PRECOMBAT_MAIN",
                "players": [],
                "stack": [],
            },
            {
                "seq": 11,
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "step": "PRECOMBAT_MAIN",
                "players": [],
                "stack": [],
            },
            {
                "seq": 12,
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "step": "PRECOMBAT_MAIN",
                "players": [],
                "stack": [],
            },
        ]
        llm_events = [
            {
                "type": "tool_call",
                "tool": "pass_priority",
                "player": "Alice",
                "ts": "T01",
                "gameSeq": 10,
                "args": {},
                "result": json.dumps(
                    {
                        "action_pending": True,
                        "action_type": "GAME_SELECT",
                        "response_type": "index",
                        "message": "Choose replacement effect",
                        "choices": [{"index": 0, "description": "Pick effect"}],
                    }
                ),
            },
            {
                "type": "llm_response",
                "player": "Alice",
                "ts": "T02",
                "reasoning": "pick the effect",
            },
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "ts": "T03",
                "gameSeq": 11,
                "args": {"choice": "0"},
                "result": json.dumps(
                    {
                        "action_pending": True,
                        "action_type": "GAME_CHOOSE_CHOICE",
                        "response_type": "index",
                        "message": "Choose color",
                        "choices": [
                            {"index": 0, "description": "Blue"},
                            {"index": 1, "description": "Black"},
                        ],
                        "success": True,
                        "action_taken": "selected_0",
                    }
                ),
            },
            {
                "type": "llm_response",
                "player": "Alice",
                "ts": "T04",
                "reasoning": "black",
            },
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "ts": "T05",
                "gameSeq": 11,
                "args": {"choice": "Black"},
                "result": json.dumps({"error": "Unknown short ID: Black"}),
            },
            {
                "type": "llm_response",
                "player": "Alice",
                "ts": "T06",
                "reasoning": "use text",
            },
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "ts": "T07",
                "gameSeq": 12,
                "args": {"text": "Black"},
                "result": json.dumps(
                    {
                        "action_pending": True,
                        "action_type": "GAME_SELECT",
                        "response_type": "boolean",
                        "message": "Play spells and abilities",
                        "choices": [],
                        "success": True,
                        "action_taken": "selected_choice_text_Black",
                    }
                ),
            },
        ]

        decisions = _build_decisions(snapshots, [], llm_events, harness_epoch=40)

        assert len(decisions) == 2
        assert decisions[1]["message"] == "Choose color"
        assert decisions[1]["chosenArgs"] == {"text": "Black"}
        assert decisions[1]["actionResult"]["action_taken"] == "selected_choice_text_Black"
        assert decisions[1]["actionSeq"] == 12
        assert decisions[1]["llmEventIndices"] == [2, 3, 4, 5, 6]

    def test_backfill_game_force_rebuilds_existing_decisions(self, tmp_path) -> None:
        path = tmp_path / "game_retry.json5"
        payload = _make_v6_export()
        payload["snapshots"] = [
            {
                "seq": 10,
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "step": "PRECOMBAT_MAIN",
                "active_player": "Alice",
                "priority_player": "Alice",
                "players": [],
                "stack": [],
            },
            {
                "seq": 11,
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "step": "PRECOMBAT_MAIN",
                "active_player": "Alice",
                "priority_player": "Alice",
                "players": [],
                "stack": [],
            },
        ]
        payload["actions"] = []
        payload["llmEvents"] = [
            {
                "type": "tool_call",
                "tool": "pass_priority",
                "player": "Alice",
                "ts": "T01",
                "gameSeq": 10,
                "args": {},
                "result": json.dumps(
                    {
                        "action_pending": True,
                        "action_type": "GAME_CHOOSE_CHOICE",
                        "response_type": "index",
                        "message": "Choose color",
                        "choices": [
                            {"index": 0, "description": "Blue"},
                            {"index": 1, "description": "Black"},
                        ],
                    }
                ),
            },
            {
                "type": "llm_response",
                "player": "Alice",
                "ts": "T02",
                "reasoning": "black",
            },
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "ts": "T03",
                "gameSeq": 10,
                "args": {"choice": "Black"},
                "result": json.dumps({"error": "Unknown short ID: Black"}),
            },
            {
                "type": "llm_response",
                "player": "Alice",
                "ts": "T04",
                "reasoning": "use text",
            },
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "ts": "T05",
                "gameSeq": 11,
                "args": {"text": "Black"},
                "result": json.dumps({"success": True, "action_taken": "selected_choice_text_Black"}),
            },
        ]
        payload["decisions"] = [
            {
                "index": 0,
                "snapshotIndex": 0,
                "player": "Alice",
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "actionType": "GAME_CHOOSE_CHOICE",
                "responseType": "index",
                "message": "Choose color",
                "choices": [],
                "choiceCount": 0,
                "isForced": True,
                "chosenArgs": {"choice": "Black"},
                "actionResult": {"error": "Unknown short ID: Black"},
                "llmEventIndices": [0, 1, 2],
                "subsequentActions": [],
            }
        ]
        path.write_text(json.dumps(payload))

        status, count = backfill_game(path, force=True)

        assert status == "updated"
        assert count == 1
        updated = loads_json5(path.read_text())
        assert updated["decisions"][0]["chosenArgs"] == {"text": "Black"}
        assert updated["decisions"][0]["actionResult"] == {
            "success": True,
            "action_taken": "selected_choice_text_Black",
        }


def _make_v5_export() -> dict:
    """Create a minimal v5 export with llmTrace."""
    v5 = _make_v4_export()
    # Apply v4->v5 migration (normalises chosenArgs)
    v5 = v4_to_v5.up(v5)
    v5["llmTrace"] = [
        {
            "ts": "2026-03-01T12:00:00-08:00",
            "player": "Alice",
            "request": {"model": "test-model", "max_tokens": 1000},
            "response": {"id": "resp_1", "choices": [], "usage": {}},
        }
    ]
    return v5


class TestMigrateV5V6:
    def test_v5_to_v6_up_removes_llm_trace(self) -> None:
        v6 = v5_to_v6.up(_make_v5_export())
        assert v6["version"] == 6
        assert "llmTrace" not in v6

    def test_v6_to_v5_down_restores_empty_llm_trace(self) -> None:
        v5 = _make_v5_export()
        v6 = v5_to_v6.up(json.loads(json.dumps(v5)))
        v5_restored = v5_to_v6.down(v6)
        assert v5_restored["version"] == 5
        assert v5_restored["llmTrace"] == []

    def test_round_trip_preserves_v5_structure(self) -> None:
        """v5 -> v6 -> v5 should preserve all fields except llmTrace content."""
        v5_original = _make_v5_export()
        v5_without_trace = json.loads(json.dumps(v5_original))
        v5_without_trace["llmTrace"] = []  # down() restores empty

        v6 = v5_to_v6.up(json.loads(json.dumps(v5_original)))
        v5_restored = v5_to_v6.down(v6)

        assert json.dumps(v5_restored, sort_keys=True) == json.dumps(v5_without_trace, sort_keys=True)


class TestGameExportHelpers:
    def test_load_raw_game_export_handles_json_and_gz(self, tmp_path: Path) -> None:
        payload = _make_v6_export()
        json_path = tmp_path / "game_test.json5"
        gz_path = tmp_path / "game_test_copy.json5.gz"
        json_text = json.dumps(payload)

        json_path.write_text(json_text)
        gz_path.write_bytes(gzip.compress(json_text.encode()))

        assert load_raw_game_export(json_path)["id"] == payload["id"]
        assert load_raw_game_export(gz_path)["id"] == payload["id"]

    def test_write_raw_game_export_switches_to_json_and_removes_gz(self, tmp_path: Path) -> None:
        payload = _make_v6_export()
        gz_path = tmp_path / "game_small.json5.gz"
        json_path = tmp_path / "game_small.json5"
        gz_path.write_bytes(b"stale")

        with patch("scripts.game_exports.GAME_EXPORT_GZ_THRESHOLD", 10_000):
            out_path = write_raw_game_export(gz_path, payload)

        assert out_path == json_path
        assert json_path.exists()
        assert not gz_path.exists()
        assert loads_json5(json_path.read_text())["id"] == payload["id"]

    def test_write_raw_game_export_switches_to_gz_and_removes_json(self, tmp_path: Path) -> None:
        payload = _make_v6_export()
        json_path = tmp_path / "game_large.json5"
        gz_path = tmp_path / "game_large.json5.gz"
        json_path.write_text("stale")

        with patch("scripts.game_exports.GAME_EXPORT_GZ_THRESHOLD", 1):
            out_path = write_raw_game_export(json_path, payload)

        assert out_path == gz_path
        assert gz_path.exists()
        assert not json_path.exists()
        raw = gzip.decompress(gz_path.read_bytes())
        assert loads_json5(raw.decode())["id"] == payload["id"]

    def test_write_raw_game_export_serializes_decision_support_dataclasses(self, tmp_path: Path) -> None:
        payload = _make_v6_export()
        payload["version"] = 8
        payload["season"] = 1
        payload["tournament"] = None
        payload["annotations"] = []
        payload["blunderScriptVersion"] = 0
        payload["decisions"] = [
            {
                "index": 0,
                "snapshotIndex": 0,
                "player": "Alice",
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "actionType": "play",
                "responseType": "choice",
                "message": "Play spells and abilities",
                "choices": [Choice.from_mapping({"index": 0, "name": "Memnite", "power": "1"})],
                "choiceCount": 1,
                "isForced": True,
                "llmEventIndices": [],
                "subsequentActions": [],
                "pilotContext": PilotContext.from_mapping(
                    {"untappedLands": 1, "combatPhase": None, "manaPool": {"WHITE": 1}}
                ),
                "items": [MultiAmountItem.from_mapping({"description": "Assign damage", "target": "p1"})],
            }
        ]

        out_path = write_raw_game_export(tmp_path / "game_dataclass.json5", payload)

        written = loads_json5(out_path.read_text())
        assert written["decisions"][0]["choices"][0] == {"index": 0, "name": "Memnite", "power": "1"}
        assert written["decisions"][0]["pilotContext"] == {
            "untappedLands": 1,
            "combatPhase": None,
            "manaPool": {"WHITE": 1},
        }
        assert written["decisions"][0]["items"][0] == {
            "description": "Assign damage",
            "target": "p1",
        }

    def test_write_raw_game_export_serializes_action_from_as_json_from(self, tmp_path: Path) -> None:
        payload = {
            "id": "game_test_001",
            "actions": [
                Action(seq=1, type="chat", message="hello", from_="Alice"),
            ],
        }
        json_path = tmp_path / "game_test_001.json5"

        out_path = write_raw_game_export(json_path, payload)

        written = loads_json5(out_path.read_text())
        assert written["actions"][0]["from"] == "Alice"
        assert "from_" not in written["actions"][0]

    def test_glob_game_export_paths_prefers_gz_when_both_exist(self, tmp_path: Path) -> None:
        (tmp_path / "game_a.json5").write_text("{}")
        (tmp_path / "game_b.json5.gz").write_bytes(gzip.compress(b"{}"))
        (tmp_path / "game_c.json5").write_text("{}")
        (tmp_path / "game_c.json5.gz").write_bytes(gzip.compress(b"{}"))

        paths = glob_game_export_paths(tmp_path)

        assert [path.name for path in paths] == [
            "game_a.json5",
            "game_b.json5.gz",
            "game_c.json5.gz",
        ]


def _make_v6_export(
    *,
    sparse_player_stats: bool = False,
    sparse_season: bool = False,
) -> dict:
    """Create a minimal v6 export with representative llmEvents."""
    v6 = v5_to_v6.up(_make_v5_export())
    v6["players"] = [
        {"name": "Alice", "type": "pilot"},
        {"name": "Bob", "type": "cpu"},
    ]
    v6["llmEvents"] = [
        {
            "ts": "2026-03-01T12:00:00-08:00",
            "player": "Alice",
            "type": "game_start",
            "model": "test-model",
            "availableTools": ["pass_priority"],
        },
        {
            "ts": "2026-03-01T12:00:01-08:00",
            "player": "Alice",
            "type": "tool_call",
            "tool": "pass_priority",
            "args": "",
            "result": '{"success": true}',
        },
        {
            "ts": "2026-03-01T12:00:03-08:00",
            "player": "Alice",
            "type": "tool_call",
            "tool": "choose_action",
            "args": "",
            "result": '{"success": false}',
        },
        {
            "ts": "2026-03-01T12:00:06-08:00",
            "player": "Alice",
            "type": "llm_response",
            "reasoning": "",
            "toolCalls": [],
        },
    ]
    v6["season"] = 1
    v6["tournament"] = None

    if not sparse_player_stats:
        v6["players"][0]["toolCallsOk"] = 1
        v6["players"][0]["toolCallsFailed"] = 1
        v6["players"][0]["thinkingTimeSecs"] = 6.0
        v6["players"][1]["toolCallsOk"] = 0
        v6["players"][1]["toolCallsFailed"] = 0
        v6["players"][1]["thinkingTimeSecs"] = 0.0

    if sparse_season:
        del v6["season"]
        del v6["tournament"]

    return v6


class TestMigrateV6V7:
    def test_v6_to_v7_up_normalizes_sparse_player_stats(self) -> None:
        v7 = v6_to_v7.up(_make_v6_export(sparse_player_stats=True, sparse_season=True))
        assert v7["version"] == 7
        assert v7["season"] == 1
        assert v7["tournament"] is None

        alice, bob = v7["players"]
        assert alice["toolCallsOk"] == 1
        assert alice["toolCallsFailed"] == 1
        assert alice["thinkingTimeSecs"] == 6.0
        assert bob["toolCallsOk"] == 0
        assert bob["toolCallsFailed"] == 0
        assert bob["thinkingTimeSecs"] == 0.0

    def test_v7_to_v6_down_keeps_normalized_fields(self) -> None:
        v7 = v6_to_v7.up(_make_v6_export(sparse_player_stats=True, sparse_season=True))
        v6 = v6_to_v7.down(v7)

        assert v6["version"] == 6
        assert v6["season"] == 1
        assert v6["tournament"] is None
        assert v6["players"][0]["toolCallsOk"] == 1
        assert v6["players"][0]["toolCallsFailed"] == 1
        assert v6["players"][0]["thinkingTimeSecs"] == 6.0

    def test_round_trip_preserves_normalized_v6_structure(self) -> None:
        v6_original = _make_v6_export()
        original_json = json.dumps(v6_original, sort_keys=True)

        v7 = v6_to_v7.up(json.loads(original_json))
        v6_restored = v6_to_v7.down(v7)

        assert json.dumps(v6_restored, sort_keys=True) == original_json


def _make_v7_export() -> dict:
    """Create a minimal v7 export with decisions and annotations."""
    return {
        "version": 7,
        "id": "game_20260301_120000",
        "timestamp": "2026-03-01T12:00:00-08:00",
        "gameType": "Two Player Duel",
        "deckType": "Constructed - Standard",
        "totalTurns": 1,
        "winner": "Alice",
        "harnessEpoch": 49,
        "youtubeUrl": "",
        "players": [
            {
                "name": "Alice",
                "type": "pilot",
                "model": "test/model",
                "toolCallsOk": 1,
                "toolCallsFailed": 0,
                "thinkingTimeSecs": 1.0,
            },
            {
                "name": "Bob",
                "type": "cpu",
                "toolCallsOk": 0,
                "toolCallsFailed": 0,
                "thinkingTimeSecs": 0.0,
            },
        ],
        "cardImages": {},
        "snapshots": [
            {
                "seq": 1,
                "ts": "2026-03-01T12:00:00-08:00",
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "step": "PRECOMBAT_MAIN",
                "active_player": "Alice",
                "priority_player": "Alice",
                "players": [],
                "stack": [],
            },
            {
                "seq": 2,
                "ts": "2026-03-01T12:00:01-08:00",
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "step": "PRECOMBAT_MAIN",
                "active_player": "Alice",
                "priority_player": "Bob",
                "players": [],
                "stack": [],
            },
        ],
        "actions": [],
        "llmEvents": [],
        "gameOver": None,
        "annotations": [
            {
                "snapshotIndex": 1,
                "player": "Alice",
                "type": "blunder",
                "severity": "minor",
                "description": "Passed instead of committing to the board.",
                "actionTaken": "Passed priority",
                "betterLine": "Cast a threat",
            }
        ],
        "blunderScriptVersion": 32,
        "season": 1,
        "tournament": None,
        "decisions": [
            {
                "index": 0,
                "snapshotIndex": 0,
                "player": "Alice",
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "actionType": "GAME_SELECT",
                "responseType": "select",
                "message": "Choose an action",
                "choices": [],
                "choiceCount": 0,
                "isForced": False,
                "llmEventIndices": [],
                "subsequentActions": [],
                "actionSeq": 1,
            }
        ],
    }


class TestMigrateV7V8:
    def test_v7_to_v8_up_adds_annotation_decision_index(self) -> None:
        v8 = v7_to_v8.up(_make_v7_export())

        assert v8["version"] == 8
        assert v8["annotations"][0]["decisionIndex"] == 0
        assert v8["annotations"][0]["snapshotIndex"] == 1

    def test_v8_to_v7_down_removes_annotation_decision_index(self) -> None:
        v8 = v7_to_v8.up(_make_v7_export())
        v7 = v7_to_v8.down(v8)

        assert v7["version"] == 7
        assert "decisionIndex" not in v7["annotations"][0]

    def test_round_trip_preserves_v7_structure(self) -> None:
        v7_original = _make_v7_export()
        original_json = json.dumps(v7_original, sort_keys=True)

        v8 = v7_to_v8.up(json.loads(original_json))
        v7_restored = v7_to_v8.down(v8)

        assert json.dumps(v7_restored, sort_keys=True) == original_json


class TestMigrationRunner:
    """Tests for the migration runner's path-finding logic."""

    def test_find_path_same_version(self) -> None:

        assert find_migration_path(4, 4, MIGRATIONS) == []

    def test_find_path_up_one_step(self) -> None:

        path = find_migration_path(3, 4, MIGRATIONS)
        assert len(path) == 1
        assert path[0][1] == "up"
        assert path[0][0].SOURCE_VERSION == 3
        assert path[0][0].TARGET_VERSION == 4

    def test_find_path_down_one_step(self) -> None:

        path = find_migration_path(4, 3, MIGRATIONS)
        assert len(path) == 1
        assert path[0][1] == "down"

    def test_find_path_up_multiple_steps(self) -> None:

        path = find_migration_path(2, 4, MIGRATIONS)
        assert len(path) == 2
        assert path[0][1] == "up"
        assert path[1][1] == "up"
        assert path[0][0].SOURCE_VERSION == 2
        assert path[1][0].SOURCE_VERSION == 3

    def test_find_path_down_multiple_steps(self) -> None:

        path = find_migration_path(4, 2, MIGRATIONS)
        assert len(path) == 2
        assert path[0][1] == "down"
        assert path[1][1] == "down"

    def test_find_path_invalid_raises(self) -> None:

        with pytest.raises(AssertionError, match="No migration path"):
            find_migration_path(1, 4, MIGRATIONS)

    def test_chain_v2_to_v4_round_trip(self) -> None:
        """v2 → v4 → v2 through the runner's chain should roundtrip."""

        v2_original = _make_v2_export()
        original_images = dict(v2_original["cardImages"])

        # v2 → v4
        data = json.loads(json.dumps(v2_original))
        with (
            patch("scripts.scryfall.collection", side_effect=_mock_collection),
            patch("scripts.scryfall.named", side_effect=_mock_named),
            patch("scripts.scryfall.search_token", side_effect=_mock_search_token),
        ):
            for module, direction in find_migration_path(2, 4, MIGRATIONS):
                func = module.up if direction == "up" else module.down
                data = func(data)

        assert data["version"] == 4
        assert "cardData" in data
        assert "season" in data

        # v4 → v2
        for module, direction in find_migration_path(4, 2, MIGRATIONS):
            func = module.up if direction == "up" else module.down
            data = func(data)

        assert data["version"] == 2
        assert "cardData" not in data
        assert "season" not in data
        assert data["cardImages"] == original_images
