"""Convention tests for checked-in config files."""

import json
from pathlib import Path
from typing import ClassVar

import pytest

from tests.weird.repo_convention_helpers import (
    CONFIGS_DIR,
    PUPPETEER_DIR,
    SPECIAL_PERSONALITY_KEYWORDS,
    SPECIAL_PRESET_KEYWORDS,
    load_json,
)


class TestAllConfigsLoad:
    @pytest.mark.parametrize(
        "config_path",
        sorted(CONFIGS_DIR.glob("*.json")),
        ids=lambda p: p.name,
    )
    def test_config_parses(self, config_path: Path) -> None:
        """Every config file must be valid JSON with a players array."""
        data = json.loads(config_path.read_text())
        assert "players" in data, f"{config_path.name} missing 'players'"
        assert isinstance(data["players"], list), f"{config_path.name} players is not a list"
        assert data["players"], f"{config_path.name} has empty players list"


class TestConfigReferencesValid:
    def test_config_presets_are_valid(self) -> None:
        """Every preset in a config player must be a special keyword or exist in presets.json."""
        presets_data = load_json(PUPPETEER_DIR / "presets.json")
        preset_names = set(presets_data["presets"])

        bad = []
        for config_path in sorted(CONFIGS_DIR.glob("*.json")):
            data = load_json(config_path)
            for i, player in enumerate(data.get("players", [])):
                preset = player.get("preset")
                if preset and preset not in SPECIAL_PRESET_KEYWORDS and preset not in preset_names:
                    bad.append(f"{config_path.name} player[{i}]: {preset!r}")

        assert not bad, "Config players reference unknown presets:\n  " + "\n  ".join(bad)

    def test_config_personalities_are_valid(self) -> None:
        """Every personality in a config player must be a special keyword or exist in personalities.json."""
        personalities = load_json(PUPPETEER_DIR / "personalities.json")
        personality_names = set(personalities)

        bad = []
        for config_path in sorted(CONFIGS_DIR.glob("*.json")):
            data = load_json(config_path)
            for i, player in enumerate(data.get("players", [])):
                personality = player.get("personality")
                if (
                    personality
                    and personality not in SPECIAL_PERSONALITY_KEYWORDS
                    and personality not in personality_names
                ):
                    bad.append(f"{config_path.name} player[{i}]: {personality!r}")

        assert not bad, "Config players reference unknown personalities:\n  " + "\n  ".join(bad)


class TestConfigDeckTypes:
    _VALID_DECK_TYPES: ClassVar[set[str]] = {
        "Constructed - Standard",
        "Constructed - Modern",
        "Constructed - Legacy",
        "Limited",
        "Variant Magic - Freeform Commander",
        "Variant Magic - Commander",
    }

    def test_deck_types_recognized(self) -> None:
        """Every deckType value in configs must be a known XMage deck type."""
        bad = []
        for config_path in sorted(CONFIGS_DIR.glob("*.json")):
            data = load_json(config_path)
            deck_type = data.get("deckType")
            if deck_type is None:
                continue
            types = deck_type if isinstance(deck_type, list) else [deck_type]
            bad.extend(f"{config_path.name}: {value!r}" for value in types if value not in self._VALID_DECK_TYPES)

        assert not bad, (
            "Configs use unrecognized deckType values (add to _VALID_DECK_TYPES if intentional):\n  " + "\n  ".join(bad)
        )
