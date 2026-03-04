"""Test v2 ↔ v3 and v3 ↔ v4 migration round-trip fidelity."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

# Insert scripts/ and puppeteer/src/ onto sys.path so we can import the migration modules
SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
PUPPETEER_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(PUPPETEER_SRC))


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


class TestMigrateRoundTrip:
    def test_v2_to_v3_adds_card_data_and_token_images(self) -> None:
        from export_game import _build_card_data

        v2 = _make_v2_export()
        original_card_images = dict(v2["cardImages"])

        with (
            patch("scryfall.collection", side_effect=_mock_collection),
            patch("scryfall.named", side_effect=_mock_named),
            patch("scryfall.search_token", side_effect=_mock_search_token),
        ):
            card_images, card_data = _build_card_data(v2["cardImages"], v2["snapshots"])

        # Token image should be added
        assert "Goblin Token" in card_images
        assert card_images["Goblin Token"].startswith("https://")

        # Original images still present
        for name, url in original_card_images.items():
            assert card_images[name] == url

        # Card data should have metadata for real cards
        assert "Lightning Bolt" in card_data
        assert card_data["Lightning Bolt"]["mana_cost"] == "{R}"
        assert card_data["Lightning Bolt"]["type_line"] == "Instant"
        assert "Mountain" in card_data
        assert card_data["Mountain"]["type_line"] == "Basic Land — Mountain"

        # Token should NOT be in card_data (only real cards)
        assert "Goblin Token" not in card_data

    def test_v3_to_v2_removes_card_data_and_tokens(self) -> None:
        from migrate_v3_to_v2 import _is_token_key

        v3 = _make_v2_export()
        v3["version"] = 3
        v3["cardData"] = {
            "Lightning Bolt": {"mana_cost": "{R}", "type_line": "Instant"},
        }
        v3["cardImages"]["Goblin Token"] = "https://cards.scryfall.io/small/front/token/goblin.jpg"

        # Simulate v3→v2 migration
        card_images = {k: v for k, v in v3["cardImages"].items() if not _is_token_key(k)}

        assert "Goblin Token" not in card_images
        assert "Lightning Bolt" in card_images
        assert "Mountain" in card_images

    def test_round_trip_preserves_v2_structure(self) -> None:
        """v2 → v3 → v2 should produce the same cardImages as the original."""
        from export_game import _build_card_data
        from migrate_v3_to_v2 import _is_token_key

        v2_original = _make_v2_export()
        original_json = json.dumps(v2_original, sort_keys=True)

        # v2 → v3
        with (
            patch("scryfall.collection", side_effect=_mock_collection),
            patch("scryfall.named", side_effect=_mock_named),
            patch("scryfall.search_token", side_effect=_mock_search_token),
        ):
            card_images_v3, card_data = _build_card_data(
                dict(v2_original["cardImages"]),
                v2_original["snapshots"],
            )

        v3 = json.loads(original_json)  # Deep copy
        v3["version"] = 3
        v3["cardImages"] = card_images_v3
        v3["cardData"] = card_data

        # v3 → v2
        v2_restored = json.loads(json.dumps(v3, sort_keys=True))  # Deep copy
        v2_restored.pop("cardData", None)
        v2_restored["cardImages"] = {k: v for k, v in v2_restored["cardImages"].items() if not _is_token_key(k)}
        v2_restored["version"] = 2

        # Compare
        assert v2_restored["version"] == v2_original["version"]
        assert v2_restored["cardImages"] == v2_original["cardImages"]
        assert "cardData" not in v2_restored

    def test_trim_card_extracts_correct_fields(self) -> None:
        from export_game import _trim_card

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
        from export_game import _trim_card

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
        from export_game import _collect_card_names

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


class TestMigrateV3V4RoundTrip:
    def test_v3_to_v4_adds_season_and_tournament(self) -> None:
        from migrate_v3_to_v4 import compute_season

        v3 = _make_v3_export(harness_epoch=40)
        season = compute_season(v3["harnessEpoch"])

        assert season == 1

    def test_v3_to_v4_pre_season(self) -> None:
        from migrate_v3_to_v4 import compute_season

        v3 = _make_v3_export(harness_epoch=5)
        season = compute_season(v3["harnessEpoch"])

        assert season == 0

    def test_v3_to_v4_boundary(self) -> None:
        """Epoch exactly at MIN_LEADERBOARD_EPOCH should be season 1."""
        from migrate_v3_to_v4 import compute_season

        from puppeteer.harness_epoch import MIN_LEADERBOARD_EPOCH

        assert compute_season(MIN_LEADERBOARD_EPOCH) == 1
        assert compute_season(MIN_LEADERBOARD_EPOCH - 1) == 0

    def test_v4_to_v3_removes_season_and_tournament(self) -> None:
        v4 = _make_v3_export()
        v4["version"] = 4
        v4["season"] = 1
        v4["tournament"] = None

        # Simulate v4→v3 migration
        v3 = json.loads(json.dumps(v4))
        v3.pop("season", None)
        v3.pop("tournament", None)
        v3["version"] = 3

        assert "season" not in v3
        assert "tournament" not in v3
        assert v3["version"] == 3

    def test_round_trip_preserves_v3_structure(self) -> None:
        """v3 → v4 → v3 should produce the same structure as the original."""
        from migrate_v3_to_v4 import compute_season

        v3_original = _make_v3_export()
        original_json = json.dumps(v3_original, sort_keys=True)

        # v3 → v4
        v4 = json.loads(original_json)  # Deep copy
        v4["version"] = 4
        v4["season"] = compute_season(v4["harnessEpoch"])
        v4["tournament"] = None

        assert v4["season"] == 1
        assert v4["tournament"] is None

        # v4 → v3
        v3_restored = json.loads(json.dumps(v4, sort_keys=True))  # Deep copy
        v3_restored.pop("season", None)
        v3_restored.pop("tournament", None)
        v3_restored["version"] = 3

        # Compare
        assert json.dumps(v3_restored, sort_keys=True) == original_json

    def test_round_trip_preserves_v3_pre_season(self) -> None:
        """Round-trip with a pre-season game."""
        from migrate_v3_to_v4 import compute_season

        v3_original = _make_v3_export(harness_epoch=5)
        original_json = json.dumps(v3_original, sort_keys=True)

        # v3 → v4
        v4 = json.loads(original_json)
        v4["version"] = 4
        v4["season"] = compute_season(v4["harnessEpoch"])
        v4["tournament"] = None

        assert v4["season"] == 0

        # v4 → v3
        v3_restored = json.loads(json.dumps(v4, sort_keys=True))
        v3_restored.pop("season", None)
        v3_restored.pop("tournament", None)
        v3_restored["version"] = 3

        assert json.dumps(v3_restored, sort_keys=True) == original_json
