"""Test migration round-trip fidelity and runner path-finding."""

import json
from unittest.mock import patch

import pytest

from puppeteer.harness_epoch import MIN_LEADERBOARD_EPOCH
from schemas.migrations import MIGRATIONS, v2_to_v3, v3_to_v4, v4_to_v5, v5_to_v6
from scripts.export_game import _collect_card_names, _trim_card
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
        """Epoch exactly at MIN_LEADERBOARD_EPOCH should be season 1."""
        assert v3_to_v4.compute_season(MIN_LEADERBOARD_EPOCH) == 1
        assert v3_to_v4.compute_season(MIN_LEADERBOARD_EPOCH - 1) == 0

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
