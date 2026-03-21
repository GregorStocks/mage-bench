"""Tests for configuration dataclasses."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from puppeteer.config import (
    Config,
    CpuPlayer,
    PilotPlayer,
    SleepwalkerPlayer,
    _generate_player_name,
    _resolve_personality,
    _resolve_randoms,
    _validate_name_parts,
    load_models,
    load_personalities,
    load_presets,
    load_prompts,
    load_toolsets,
    resolve_preset,
)


def test_config_defaults():
    config = Config()
    assert config.server == "localhost"
    assert config.start_port == 17171
    assert config.sleepwalker_players == []
    assert config.pilot_players == []


def test_config_load_players_from_json():
    config_data = {
        "players": [
            {"type": "sleepwalker", "name": "spud"},
            {"type": "cpu", "name": "skynet"},
            {"type": "pilot", "name": "ace", "preset": "test-preset", "provider": "openai"},
        ],
        "matchTimeLimit": "MIN__60",
        "gameType": "Two Player Duel",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Create presets + prompts so preset resolution works
        presets = {
            "presets": {"test-preset": {"model": "test/model", "system_prompt": "default"}},
            "gauntlet": [],
        }
        (tmpdir_path / "presets.json").write_text(json.dumps(presets))
        (tmpdir_path / "prompts.json").write_text(json.dumps({"default": "You are a test player."}))
        (tmpdir_path / "personalities.json").write_text("{}")
        (tmpdir_path / "models.json").write_text(
            json.dumps({"models": [{"id": "test/model", "name": "Test Model", "name_part": "TModel"}]})
        )

        config_path = tmpdir_path / "config.json"
        config_path.write_text(json.dumps(config_data))

        config = Config(config_file=config_path)
        config.load_config()

        assert len(config.sleepwalker_players) == 1
        assert config.sleepwalker_players[0].name == "spud"
        assert len(config.cpu_players) == 1
        assert isinstance(config.cpu_players[0], CpuPlayer)
        assert len(config.pilot_players) == 1
        assert isinstance(config.pilot_players[0], PilotPlayer)
        assert config.pilot_players[0].model == "test/model"
        assert config.pilot_players[0].provider == "openai"
        assert config.match_time_limit == "MIN__60"
        assert config.game_type == "Two Player Duel"


def test_config_rejects_base_url_field():
    config_data = {
        "players": [
            {
                "type": "pilot",
                "name": "ace",
                "preset": "test-preset",
                "base_url": "https://api.openai.com/v1",
            }
        ],
        "gameType": "Two Player Duel",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        presets = {
            "presets": {"test-preset": {"model": "test/model", "system_prompt": "default"}},
            "gauntlet": [],
        }
        (tmpdir_path / "presets.json").write_text(json.dumps(presets))
        (tmpdir_path / "prompts.json").write_text(json.dumps({"default": "You are a test player."}))
        (tmpdir_path / "personalities.json").write_text("{}")
        (tmpdir_path / "models.json").write_text(
            json.dumps({"models": [{"id": "test/model", "name": "Test Model", "name_part": "TModel"}]})
        )

        config_path = tmpdir_path / "config.json"
        config_path.write_text(json.dumps(config_data))

        config = Config(config_file=config_path)
        with pytest.raises(AssertionError, match="provider"):
            config.load_config()


def test_player_dataclass_fields():
    player = SleepwalkerPlayer(name="test")
    assert player.name == "test"
    assert player.deck is None

    player_with_deck = SleepwalkerPlayer(name="test", deck="decks/test.dck")
    assert player_with_deck.deck == "decks/test.dck"


def test_get_players_config_json_roundtrip():
    """Load players from JSON, serialize back, verify structure is preserved."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        presets = {
            "presets": {"test-preset": {"model": "test/model", "system_prompt": "default"}},
            "gauntlet": [],
        }
        (tmpdir_path / "presets.json").write_text(json.dumps(presets))
        (tmpdir_path / "prompts.json").write_text(json.dumps({"default": "Test prompt."}))
        (tmpdir_path / "personalities.json").write_text("{}")
        (tmpdir_path / "models.json").write_text(
            json.dumps({"models": [{"id": "test/model", "name": "Test Model", "name_part": "TModel"}]})
        )

        config_data = {
            "players": [
                {"type": "sleepwalker", "name": "spud", "deck": "decks/burn.dck"},
                {"type": "pilot", "name": "ace", "preset": "test-preset", "deck": "decks/control.dck"},
                {"type": "cpu", "name": "skynet"},
            ],
            "gameType": "Two Player Duel",
            "deckType": "Constructed - Legacy",
        }

        config_path = tmpdir_path / "config.json"
        config_path.write_text(json.dumps(config_data))

        config = Config(config_file=config_path)
        config.load_config()
        result = json.loads(config.get_players_config_json())

        assert result["gameType"] == "Two Player Duel"
        assert result["deckType"] == "Constructed - Legacy"
        names = [p["name"] for p in result["players"]]
        assert "spud" in names
        assert "ace" in names
        assert "skynet" in names


def test_get_players_config_json_empty():
    """No players should return empty string."""
    config = Config()
    assert config.get_players_config_json() == ""


def test_config_default_player_name():
    """Players without a name should get 'player-{index}' as default."""
    config_data = {
        "players": [
            {"type": "sleepwalker"},
            {"type": "cpu"},
        ],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(config_data, f)
        config_path = Path(f.name)

    try:
        config = Config(config_file=config_path)
        config.load_config()
        assert config.sleepwalker_players[0].name == "player-0"
        assert config.cpu_players[0].name == "player-1"
    finally:
        config_path.unlink()


def test_run_tag_no_config_file_raises():
    config = Config()
    with pytest.raises(AssertionError, match="run_tag requires config_file"):
        _ = config.run_tag


def test_run_tag_jumpstart_dumb():
    config = Config(config_file=Path("configs/jumpstart-dumb.json"))
    assert config.run_tag == "jumpstart-dumb"


def test_run_tag_round_robin_1v1():
    config = Config(config_file=Path("configs/round-robin-1v1.json"))
    assert config.run_tag == "round-robin-1v1"


def test_run_tag_round_robin_commander():
    config = Config(config_file=Path("configs/round-robin-commander.json"))
    assert config.run_tag == "round-robin-commander"


def test_run_tag_custom():
    config = Config(config_file=Path("custom-thing.json"))
    assert config.run_tag == "custom-thing"


def test_new_game_config_preserves_runtime_options(tmp_path: Path):
    config_a = tmp_path / "a.json"
    config_b = tmp_path / "b.json"
    config_a.write_text("{}\n")
    config_b.write_text("{}\n")

    base = Config(
        server="127.0.0.1",
        start_port=18181,
        user="spectator",
        password="secret",
        server_wait=321,
        bridge_delay=9,
        log_dir=tmp_path / "logs",
        jvm_opens="--test-opens",
        jvm_rendering="-Dtest.rendering=true",
        config_file=config_a,
        observer=True,
        record=True,
        record_output=tmp_path / "recording.mov",
        num_games=4,
        debug=True,
        skip_compile=True,
        port=19191,
        timestamp="20260101_120000",
    )

    game = base.new_game_config(
        config_file=config_b,
        user="spectator2",
        num_games=2,
        port=20202,
        timestamp="20260101_130000",
    )

    assert game.server == "127.0.0.1"
    assert game.start_port == 18181
    assert game.user == "spectator2"
    assert game.password == "secret"
    assert game.server_wait == 321
    assert game.bridge_delay == 9
    assert game.log_dir == tmp_path / "logs"
    assert game.jvm_opens == "--test-opens"
    assert game.jvm_rendering == "-Dtest.rendering=true"
    assert game.config_file == config_b
    assert game.observer is True
    assert game.record is True
    assert game.record_output == tmp_path / "recording.mov"
    assert game.num_games == 2
    assert game.debug is True
    assert game.skip_compile is True
    assert game.port == 20202
    assert game.timestamp == "20260101_130000"
    assert game.pilot_players == []


# --- Preset tests ---

SAMPLE_PRESETS = {
    "presets": {
        "fast-medium": {"model": "test/model-a", "reasoning_effort": "medium", "system_prompt": "default"},
        "slow-high": {"model": "test/model-b", "reasoning_effort": "high", "system_prompt": "default"},
        "bare": {"model": "test/model-c", "system_prompt": "default"},
    },
    "gauntlet": ["fast-medium", "slow-high"],
}

SAMPLE_PROMPTS: dict[str, str] = {
    "default": "You are a test player.",
}


def test_preset_resolves_model_and_effort():
    """Preset should set model and reasoning_effort on player."""
    player = PilotPlayer(name="test", preset="fast-medium")
    resolve_preset(player, SAMPLE_PRESETS, SAMPLE_PROMPTS)
    assert player.model == "test/model-a"
    assert player.reasoning_effort == "medium"
    assert player.system_prompt == "You are a test player."


def test_preset_without_reasoning_effort():
    """Preset without reasoning_effort should leave it None."""
    player = PilotPlayer(name="test", preset="bare")
    resolve_preset(player, SAMPLE_PRESETS, SAMPLE_PROMPTS)
    assert player.model == "test/model-c"
    assert player.reasoning_effort is None


def test_preset_unknown_raises():
    """Unknown preset name should raise ValueError."""
    player = PilotPlayer(name="test", preset="nonexistent")
    with pytest.raises(ValueError, match="Unknown preset"):
        resolve_preset(player, SAMPLE_PRESETS, SAMPLE_PROMPTS)


def test_preset_unknown_prompt_raises():
    """Preset referencing unknown prompt key should raise ValueError."""
    presets = {"presets": {"bad": {"model": "test/m", "system_prompt": "missing"}}, "gauntlet": []}
    player = PilotPlayer(name="test", preset="bad")
    with pytest.raises(ValueError, match="unknown prompt"):
        resolve_preset(player, presets, SAMPLE_PROMPTS)


def test_preset_no_preset_is_noop():
    """Player without preset should not be modified."""
    player = PilotPlayer(name="test")
    resolve_preset(player, SAMPLE_PRESETS, SAMPLE_PROMPTS)
    assert player.model is None
    assert player.reasoning_effort is None


# --- Personality tests ---

SAMPLE_PERSONALITIES = {
    "test-pal": {
        "name_part": "Pal",
        "prompt_suffix": "You are very friendly.",
    },
    "test-villain": {
        "name_part": "Villain",
        "prompt_suffix": "You are evil.",
    },
}


def test_personality_sets_prompt_suffix():
    """Personality should set prompt_suffix on player."""
    player = PilotPlayer(name="TestName", model="test/model-a")
    player.personality = "test-pal"
    _resolve_personality(player, SAMPLE_PERSONALITIES, {}, had_explicit_name=True)
    assert player.prompt_suffix == "You are very friendly."


def test_personality_does_not_set_model():
    """Personality should NOT set model (that's the preset's job)."""
    player = PilotPlayer(name="TestName")
    player.personality = "test-pal"
    _resolve_personality(player, SAMPLE_PERSONALITIES, {}, had_explicit_name=True)
    assert player.model is None


def test_personality_unknown_raises():
    """Unknown personality name should raise ValueError."""
    player = PilotPlayer(name="test")
    player.personality = "nonexistent"
    with pytest.raises(ValueError, match="Unknown personality"):
        _resolve_personality(player, SAMPLE_PERSONALITIES, {}, had_explicit_name=True)


def test_personality_explicit_name_preserved():
    """Explicit name in player JSON should be kept."""
    player = PilotPlayer(name="CustomName", model="test/model-a")
    player.personality = "test-pal"
    _resolve_personality(player, SAMPLE_PERSONALITIES, {}, had_explicit_name=True)
    assert player.name == "CustomName"


def test_personality_name_too_long_raises():
    """Name exceeding 14 chars should raise ValueError."""
    player = PilotPlayer(name="ThisNameIsTooLong")
    player.personality = "test-pal"
    with pytest.raises(ValueError, match="3-14 characters"):
        _resolve_personality(player, SAMPLE_PERSONALITIES, {}, had_explicit_name=True)


def test_load_personalities_from_file():
    """load_personalities should read a JSON file."""
    pdata = {"my-pal": {"name_part": "Pal", "prompt_suffix": "hi"}}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        (tmpdir_path / "personalities.json").write_text(json.dumps(pdata))
        config_path = tmpdir_path / "test-config.json"
        config_path.write_text("{}")

        result = load_personalities(config_path)
        assert "my-pal" in result
        assert result["my-pal"]["name_part"] == "Pal"


def test_load_presets_from_file():
    """load_presets should read a JSON file."""
    pdata = {"presets": {"x": {"model": "test/m", "system_prompt": "default"}}, "gauntlet": []}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        (tmpdir_path / "presets.json").write_text(json.dumps(pdata))
        config_path = tmpdir_path / "test-config.json"
        config_path.write_text("{}")

        result = load_presets(config_path)
        assert "presets" in result
        assert "x" in result["presets"]


def test_load_prompts_from_file():
    """load_prompts should read a JSON file."""
    pdata = {"default": "Hello world"}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        (tmpdir_path / "prompts.json").write_text(json.dumps(pdata))
        config_path = tmpdir_path / "test-config.json"
        config_path.write_text("{}")

        result = load_prompts(config_path)
        assert result["default"] == "Hello world"


def test_preset_end_to_end_config_load():
    """Full integration: config JSON with preset+personality loads correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        personalities = {
            "test-hero": {
                "name_part": "Hero",
                "prompt_suffix": "You are heroic.",
            }
        }
        (tmpdir_path / "personalities.json").write_text(json.dumps(personalities))

        presets = {
            "presets": {
                "test-preset": {"model": "test/hero-model", "reasoning_effort": "medium", "system_prompt": "default"},
            },
            "gauntlet": [],
        }
        (tmpdir_path / "presets.json").write_text(json.dumps(presets))
        (tmpdir_path / "prompts.json").write_text(json.dumps({"default": "Be a great player."}))
        (tmpdir_path / "models.json").write_text(
            json.dumps({"models": [{"id": "test/hero-model", "name": "Hero Model", "name_part": "HeroM"}]})
        )

        config_data = {
            "players": [
                {"type": "pilot", "preset": "test-preset", "personality": "test-hero", "deck": "random"},
                {
                    "type": "pilot",
                    "name": "Override",
                    "preset": "test-preset",
                    "personality": "test-hero",
                    "deck": "random",
                },
            ]
        }
        config_path = tmpdir_path / "config.json"
        config_path.write_text(json.dumps(config_data))

        config = Config(config_file=config_path)
        config.load_config()

        assert len(config.pilot_players) == 2

        # First player: model from preset, prompt_suffix from personality, name generated
        p1 = config.pilot_players[0]
        assert p1.model == "test/hero-model"
        assert p1.prompt_suffix == "You are heroic."
        assert p1.reasoning_effort == "medium"
        assert p1.system_prompt == "Be a great player."
        assert p1.personality == "test-hero"
        assert p1.name == "HeroM Hero"

        # Second player: explicit name overrides generated name
        p2 = config.pilot_players[1]
        assert p2.name == "Override"
        assert p2.model == "test/hero-model"
        assert p2.prompt_suffix == "You are heroic."


# --- Random resolution tests ---

SAMPLE_MODELS_DATA = {
    "models": [
        {"id": "test/model-a", "name": "Model A", "name_part": "ModA"},
        {"id": "test/model-b", "name": "Model B", "name_part": "ModB"},
        {"id": "test/model-c", "name": "Model C", "name_part": "ModC"},
    ],
}

SAMPLE_PRESETS_WITH_POOL = {
    "presets": {
        "preset-a": {
            "model": "test/model-a",
            "status": "active",
            "reasoning_effort": "medium",
            "system_prompt": "default",
        },
        "preset-b": {
            "model": "test/model-b",
            "status": "active",
            "reasoning_effort": "high",
            "system_prompt": "default",
        },
        "preset-c": {"model": "test/model-c", "status": "active", "system_prompt": "default"},
    },
}

SAMPLE_PERSONALITIES_WITH_PARTS = {
    "hero": {
        "name_part": "Hero",
        "prompt_suffix": "You are heroic.",
    },
    "chill": {
        "name_part": "Chill",
        "prompt_suffix": "You are chill.",
    },
    "nerd": {
        "name_part": "Nerd",
        "prompt_suffix": "You are nerdy.",
    },
}


def test_validate_name_parts_valid():
    """Valid name_part combos should not raise."""
    _validate_name_parts(SAMPLE_PERSONALITIES_WITH_PARTS, SAMPLE_PRESETS_WITH_POOL, SAMPLE_MODELS_DATA)


def test_validate_name_parts_catches_overflow():
    """name_part combo > 14 chars should raise ValueError."""
    bad_personalities = {
        "longname": {"name_part": "TooLong!", "prompt_suffix": "hi"},
    }
    bad_presets = {
        "presets": {"p": {"model": "test/m", "status": "active", "system_prompt": "default"}},
    }
    bad_models = {
        "models": [{"id": "test/m", "name": "M", "name_part": "Longish"}],
    }
    # "Longish TooLong!" = 16 chars
    with pytest.raises(ValueError, match="Invalid name_part combinations"):
        _validate_name_parts(bad_personalities, bad_presets, bad_models)


def test_validate_name_parts_missing_model():
    """Active preset referencing unknown model should raise."""
    bad_presets = {
        "presets": {"p": {"model": "test/missing", "status": "active", "system_prompt": "default"}},
    }
    bad_models: dict = {"models": []}
    with pytest.raises(ValueError, match="not found in models"):
        _validate_name_parts(SAMPLE_PERSONALITIES_WITH_PARTS, bad_presets, bad_models)


def test_validate_name_parts_real_data():
    """Validate actual personalities.json x presets.json x models.json name_part combos all fit."""
    personalities = load_personalities(None)
    presets_data = load_presets(None)
    models_data = load_models(None)
    if not personalities or not presets_data or not models_data:
        pytest.skip("personalities.json, presets.json, or models.json not found")
    # Should not raise — if it does, we have a real misconfiguration
    _validate_name_parts(personalities, presets_data, models_data)


def test_generate_player_name():
    """Name should be '{model_part} {personality_part}'."""
    name = _generate_player_name("test/model-a", "hero", SAMPLE_MODELS_DATA, SAMPLE_PERSONALITIES_WITH_PARTS)
    assert name == "ModA Hero"


def test_generate_player_name_requires_known_model():
    """Unknown model IDs should fail fast."""
    with pytest.raises(AssertionError, match="Unknown model"):
        _generate_player_name("unknown/model", "hero", SAMPLE_MODELS_DATA, SAMPLE_PERSONALITIES_WITH_PARTS)


def test_generate_player_name_requires_known_personality():
    """Unknown personalities should fail fast."""
    with pytest.raises(AssertionError, match="Unknown personality"):
        _generate_player_name("test/model-a", "unknown", SAMPLE_MODELS_DATA, SAMPLE_PERSONALITIES_WITH_PARTS)


def test_resolve_randoms_picks_personality_and_preset():
    """Random resolution should pick concrete personality and preset."""
    player = PilotPlayer(name="player-0", personality="random", preset="random")
    players = [(player, False)]

    with patch("puppeteer.config.random.choice", side_effect=["hero", "preset-b"]):
        _resolve_randoms(
            players, SAMPLE_PERSONALITIES_WITH_PARTS, SAMPLE_PRESETS_WITH_POOL, SAMPLE_PROMPTS, SAMPLE_MODELS_DATA
        )

    assert player.personality == "hero"
    assert player.preset == "preset-b"
    assert player.model == "test/model-b"
    assert player.prompt_suffix == "You are heroic."
    assert player.name == "ModB Hero"


def test_resolve_randoms_no_duplicate_personalities():
    """Multiple random players should get different personalities."""
    p1 = PilotPlayer(name="p0", personality="random", preset="random")
    p2 = PilotPlayer(name="p1", personality="random", preset="random")
    players = [(p1, False), (p2, False)]

    choices = ["hero", "preset-a", "chill", "preset-b"]
    with patch("puppeteer.config.random.choice", side_effect=choices):
        _resolve_randoms(
            players, SAMPLE_PERSONALITIES_WITH_PARTS, SAMPLE_PRESETS_WITH_POOL, SAMPLE_PROMPTS, SAMPLE_MODELS_DATA
        )

    assert p1.personality != p2.personality
    assert p1.preset != p2.preset


def test_resolve_randoms_explicit_preset_not_randomized():
    """Explicit preset should not be replaced by random."""
    player = PilotPlayer(name="player-0", personality="random", preset="preset-c")
    players = [(player, False)]

    with patch("puppeteer.config.random.choice", return_value="nerd"):
        _resolve_randoms(
            players, SAMPLE_PERSONALITIES_WITH_PARTS, SAMPLE_PRESETS_WITH_POOL, SAMPLE_PROMPTS, SAMPLE_MODELS_DATA
        )

    assert player.preset == "preset-c"
    assert player.model == "test/model-c"
    assert player.personality == "nerd"
    assert player.name == "ModC Nerd"


def test_resolve_randoms_explicit_name_preserved():
    """Explicit name in config should not be overwritten."""
    player = PilotPlayer(name="MyCustom", personality="random", preset="random")
    players = [(player, True)]  # had_explicit_name=True

    with patch("puppeteer.config.random.choice", side_effect=["hero", "preset-a"]):
        _resolve_randoms(
            players, SAMPLE_PERSONALITIES_WITH_PARTS, SAMPLE_PRESETS_WITH_POOL, SAMPLE_PROMPTS, SAMPLE_MODELS_DATA
        )

    assert player.name == "MyCustom"


def test_resolve_randoms_non_random_untouched():
    """Players with non-random personality/preset should pass through normally."""
    player = PilotPlayer(name="player-0", personality="hero", preset="preset-a")
    players = [(player, False)]

    _resolve_randoms(
        players, SAMPLE_PERSONALITIES_WITH_PARTS, SAMPLE_PRESETS_WITH_POOL, SAMPLE_PROMPTS, SAMPLE_MODELS_DATA
    )

    assert player.personality == "hero"
    assert player.preset == "preset-a"
    assert player.model == "test/model-a"
    assert player.name == "ModA Hero"  # Generated from model + personality


def test_resolve_randoms_requires_known_model() -> None:
    """Resolved players should fail fast if their model is absent from models.json."""
    player = PilotPlayer(name="player-0", personality="hero", preset="preset-a")
    players = [(player, False)]

    with pytest.raises(AssertionError, match="Unknown model"):
        _resolve_randoms(
            players,
            SAMPLE_PERSONALITIES_WITH_PARTS,
            SAMPLE_PRESETS_WITH_POOL,
            SAMPLE_PROMPTS,
            {"models": []},
        )


def test_resolve_randoms_cross_game_dedup():
    """Random resolution should re-roll personality to avoid cross-game name collisions."""
    player = PilotPlayer(name="player-0", personality="random", preset="random")
    players = [(player, False)]
    used_names = {"ModA Hero"}  # "hero" + "preset-a" would produce this name

    # First call returns "hero" (collides), second call returns "chill" (unique)
    choices = ["hero", "preset-a", "chill"]
    with patch("puppeteer.config.random.choice", side_effect=choices):
        _resolve_randoms(
            players,
            SAMPLE_PERSONALITIES_WITH_PARTS,
            SAMPLE_PRESETS_WITH_POOL,
            SAMPLE_PROMPTS,
            SAMPLE_MODELS_DATA,
            cross_game_used_names=used_names,
        )

    assert player.name == "ModA Chill"
    assert player.personality == "chill"


def test_resolve_randoms_cross_game_dedup_crashes_when_exhausted():
    """Should crash when all personalities collide with cross-game names."""
    player = PilotPlayer(name="player-0", personality="random", preset="random")
    players = [(player, False)]
    # All possible names for preset-a are taken
    used_names = {"ModA Hero", "ModA Chill", "ModA Nerd"}

    choices = ["hero", "preset-a", "chill", "nerd"]
    with (
        patch("puppeteer.config.random.choice", side_effect=choices),
        pytest.raises(AssertionError, match="Cannot generate unique player name"),
    ):
        _resolve_randoms(
            players,
            SAMPLE_PERSONALITIES_WITH_PARTS,
            SAMPLE_PRESETS_WITH_POOL,
            SAMPLE_PROMPTS,
            SAMPLE_MODELS_DATA,
            cross_game_used_names=used_names,
        )


def test_random_end_to_end_config_load():
    """Full integration: config with random values resolves to concrete players."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        personalities = {
            "alpha": {
                "name_part": "Alpha",
                "prompt_suffix": "You are alpha.",
            },
            "beta": {
                "name_part": "Beta",
                "prompt_suffix": "You are beta.",
            },
        }
        (tmpdir_path / "personalities.json").write_text(json.dumps(personalities))

        presets = {
            "presets": {
                "fast-med": {"model": "test/fast", "reasoning_effort": "medium", "system_prompt": "default"},
                "smart-med": {"model": "test/smart", "reasoning_effort": "medium", "system_prompt": "default"},
            },
            "gauntlet": ["fast-med", "smart-med"],
        }
        (tmpdir_path / "presets.json").write_text(json.dumps(presets))
        (tmpdir_path / "prompts.json").write_text(json.dumps({"default": "Test prompt."}))

        models = {
            "models": [
                {"id": "test/fast", "name": "Fast", "name_part": "Fast"},
                {"id": "test/smart", "name": "Smart", "name_part": "Smart"},
            ],
        }
        (tmpdir_path / "models.json").write_text(json.dumps(models))

        config_data = {
            "matchTimeLimit": "MIN__60",
            "players": [
                {"type": "pilot", "preset": "random", "personality": "random", "deck": "random"},
                {"type": "pilot", "preset": "random", "personality": "random", "deck": "random"},
            ],
        }
        config_path = tmpdir_path / "config.json"
        config_path.write_text(json.dumps(config_data))

        with patch("puppeteer.config.random.choice", side_effect=["alpha", "fast-med", "beta", "smart-med"]):
            config = Config(config_file=config_path)
            config.load_config()

        assert len(config.pilot_players) == 2
        p1, p2 = config.pilot_players

        assert p1.personality == "alpha"
        assert p1.preset == "fast-med"
        assert p1.model == "test/fast"
        assert p1.name == "Fast Alpha"
        assert p1.prompt_suffix == "You are alpha."

        assert p2.personality == "beta"
        assert p2.preset == "smart-med"
        assert p2.model == "test/smart"
        assert p2.name == "Smart Beta"
        assert p2.prompt_suffix == "You are beta."


# --- Format-aware random deck selection tests ---


def _make_deck_registry(root: Path, fmt_dir: str, decks: dict[str, list[str]]) -> None:
    """Create a deck registry directory with JSON files."""
    reg_dir = root / "data" / "decks" / fmt_dir
    reg_dir.mkdir(parents=True)
    (root / "tmp").mkdir(parents=True, exist_ok=True)
    for name, cards in decks.items():
        slug = name.lower().replace(" ", "-")
        data = {"name": name, "strategy": "", "cards": cards}
        (reg_dir / f"{slug}.json").write_text(json.dumps(data))


def test_resolve_random_decks_legacy_format():
    """With deckType='Constructed - Legacy', decks should come from Legacy dir."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_deck_registry(
            root,
            "legacy",
            {
                "Burn": ["4 [M21:1] Lightning Bolt"],
                "Delver": ["4 [ISD:1] Delver of Secrets"],
            },
        )

        config = Config(deck_type="Constructed - Legacy")
        config.cpu_players = [CpuPlayer(name="cpu1", deck="random")]
        config.resolve_random_decks(root)

        assert config.cpu_players[0].deck is not None
        assert config.cpu_players[0].deck.endswith(".dck")
        assert config.cpu_players[0].deck_name in ("Burn", "Delver")


def test_resolve_random_decks_modern_format():
    """With deckType='Constructed - Modern', decks should come from Modern dir."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_deck_registry(root, "modern", {"Burn": ["4 [M21:1] Lightning Bolt"]})

        config = Config(deck_type="Constructed - Modern")
        config.cpu_players = [CpuPlayer(name="cpu1", deck="random")]
        config.resolve_random_decks(root)

        assert config.cpu_players[0].deck.endswith(".dck")
        assert config.cpu_players[0].deck_name == "Burn"


def test_resolve_random_decks_commander_format():
    """With deckType='Variant Magic - Commander', decks should come from commander dir."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_deck_registry(root, "commander", {"Precon": ["1 [CMD:1] Sol Ring"]})

        config = Config(deck_type="Variant Magic - Commander")
        config.cpu_players = [CpuPlayer(name="cpu1", deck="random")]
        config.resolve_random_decks(root)

        assert config.cpu_players[0].deck.endswith(".dck")
        assert config.cpu_players[0].deck_name == "Precon"


def test_resolve_random_decks_no_duplicate_decks():
    """Two players with random decks should get different decks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_deck_registry(
            root,
            "legacy",
            {
                "DeckA": ["4 [M21:1] Card A"],
                "DeckB": ["4 [M21:2] Card B"],
                "DeckC": ["4 [M21:3] Card C"],
            },
        )

        config = Config(deck_type="Constructed - Legacy")
        config.cpu_players = [
            CpuPlayer(name="cpu1", deck="random"),
            CpuPlayer(name="cpu2", deck="random"),
        ]
        config.resolve_random_decks(root)

        assert config.cpu_players[0].deck != config.cpu_players[1].deck
        assert config.cpu_players[0].deck_name != config.cpu_players[1].deck_name


# --- Toolset tests ---

SAMPLE_TOOLSETS = {
    "default": ["pass_priority", "get_action_choices", "choose_action", "get_game_state"],
    "minimal": ["pass_priority", "choose_action"],
}


def test_load_toolsets_from_file():
    """load_toolsets should read a JSON file."""
    tdata = {"basic": ["pass_priority", "choose_action"]}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        (tmpdir_path / "toolsets.json").write_text(json.dumps(tdata))
        config_path = tmpdir_path / "test-config.json"
        config_path.write_text("{}")

        result = load_toolsets(config_path)
        assert "basic" in result
        assert result["basic"] == ["pass_priority", "choose_action"]


def test_preset_resolves_toolset():
    """Preset with toolset should set tools on player."""
    presets = {
        "presets": {
            "with-tools": {
                "model": "test/model",
                "system_prompt": "default",
                "toolset": "minimal",
            }
        },
        "gauntlet": [],
    }
    player = PilotPlayer(name="test", preset="with-tools")
    resolve_preset(player, presets, SAMPLE_PROMPTS, SAMPLE_TOOLSETS)
    assert player.tools == ["pass_priority", "choose_action"]


def test_preset_without_toolset_leaves_none():
    """Preset without toolset key should leave tools as None."""
    player = PilotPlayer(name="test", preset="fast-medium")
    resolve_preset(player, SAMPLE_PRESETS, SAMPLE_PROMPTS, SAMPLE_TOOLSETS)
    assert player.tools is None


def test_player_tools_override_preset_toolset():
    """Player-level tools should win over preset toolset."""
    presets = {
        "presets": {
            "with-tools": {
                "model": "test/model",
                "system_prompt": "default",
                "toolset": "default",
            }
        },
        "gauntlet": [],
    }
    player = PilotPlayer(
        name="test",
        preset="with-tools",
        tools=["pass_priority", "get_game_state"],
    )
    resolve_preset(player, presets, SAMPLE_PROMPTS, SAMPLE_TOOLSETS)
    # Player-level tools should win
    assert player.tools == ["pass_priority", "get_game_state"]


def test_preset_unknown_toolset_raises():
    """Preset referencing unknown toolset should raise ValueError."""
    presets = {
        "presets": {"bad": {"model": "test/m", "system_prompt": "default", "toolset": "nonexistent"}},
        "gauntlet": [],
    }
    player = PilotPlayer(name="test", preset="bad")
    with pytest.raises(ValueError, match="unknown toolset"):
        resolve_preset(player, presets, SAMPLE_PROMPTS, SAMPLE_TOOLSETS)


def test_tools_loaded_from_config_json():
    """tools field in player JSON should populate PilotPlayer.tools."""
    config_data = {
        "players": [
            {
                "type": "pilot",
                "name": "custom",
                "preset": "test-preset",
                "tools": ["pass_priority", "get_game_state"],
            },
        ],
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        presets = {
            "presets": {"test-preset": {"model": "test/m", "system_prompt": "default"}},
            "gauntlet": [],
        }
        (tmpdir_path / "presets.json").write_text(json.dumps(presets))
        (tmpdir_path / "prompts.json").write_text(json.dumps({"default": "Test."}))
        (tmpdir_path / "personalities.json").write_text("{}")
        (tmpdir_path / "models.json").write_text(
            json.dumps({"models": [{"id": "test/m", "name": "Test Model", "name_part": "TModel"}]})
        )
        (tmpdir_path / "toolsets.json").write_text("{}")

        config_path = tmpdir_path / "config.json"
        config_path.write_text(json.dumps(config_data))

        config = Config(config_file=config_path)
        config.load_config()

        assert config.pilot_players[0].tools == ["pass_priority", "get_game_state"]


def test_tools_none_when_not_specified():
    """Player without tools should have tools=None when preset has no toolset."""
    config_data = {
        "players": [
            {"type": "pilot", "name": "plain", "preset": "test-preset"},
        ],
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        presets = {
            "presets": {"test-preset": {"model": "test/m", "system_prompt": "default"}},
            "gauntlet": [],
        }
        (tmpdir_path / "presets.json").write_text(json.dumps(presets))
        (tmpdir_path / "prompts.json").write_text(json.dumps({"default": "Test."}))
        (tmpdir_path / "personalities.json").write_text("{}")
        (tmpdir_path / "models.json").write_text(
            json.dumps({"models": [{"id": "test/m", "name": "Test Model", "name_part": "TModel"}]})
        )
        (tmpdir_path / "toolsets.json").write_text("{}")

        config_path = tmpdir_path / "config.json"
        config_path.write_text(json.dumps(config_data))

        config = Config(config_file=config_path)
        config.load_config()

        assert config.pilot_players[0].tools is None


def test_resolve_random_decks_crashes_on_unresolved_choice():
    """resolve_random_decks should crash if any player still has deck='choice'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        deck_dir = root / "Mage.Client" / "release" / "sample-decks" / "Commander"
        deck_dir.mkdir(parents=True)
        (deck_dir / "Zurgo.dck").write_text("1 [CMD:1] Sol Ring\n")

        config = Config()
        config.pilot_players = [PilotPlayer(name="ace", deck="choice", model="test/m")]
        with pytest.raises(AssertionError, match="Unresolved deck='choice'"):
            config.resolve_random_decks(root)


def test_load_config_choice_on_non_pilot_crashes():
    """deck='choice' on a non-pilot player should crash during load_config."""
    config_data = {
        "players": [
            {"type": "sleepwalker", "name": "spud", "deck": "choice"},
        ],
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        (tmpdir_path / "personalities.json").write_text("{}")
        (tmpdir_path / "models.json").write_text('{"models": []}')
        (tmpdir_path / "presets.json").write_text('{"presets": {}, "gauntlet": []}')
        (tmpdir_path / "prompts.json").write_text("{}")

        config_path = tmpdir_path / "config.json"
        config_path.write_text(json.dumps(config_data))

        config = Config(config_file=config_path)
        with pytest.raises(AssertionError, match="deck='choice' requires a pilot player"):
            config.load_config()


def test_toolset_end_to_end_config_load():
    """Full integration: config with preset referencing toolset resolves correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        personalities = {"test-hero": {"name_part": "Hero", "prompt_suffix": "You are heroic."}}
        (tmpdir_path / "personalities.json").write_text(json.dumps(personalities))

        presets = {
            "presets": {
                "test-preset": {
                    "model": "test/hero-model",
                    "reasoning_effort": "medium",
                    "system_prompt": "default",
                    "toolset": "minimal",
                },
            },
            "gauntlet": [],
        }
        (tmpdir_path / "presets.json").write_text(json.dumps(presets))
        (tmpdir_path / "prompts.json").write_text(json.dumps({"default": "Be great."}))
        (tmpdir_path / "models.json").write_text(
            json.dumps({"models": [{"id": "test/hero-model", "name": "Hero", "name_part": "HeroM"}]})
        )
        (tmpdir_path / "toolsets.json").write_text(json.dumps({"minimal": ["pass_priority", "choose_action"]}))

        config_data = {
            "players": [
                {"type": "pilot", "preset": "test-preset", "personality": "test-hero"},
            ]
        }
        config_path = tmpdir_path / "config.json"
        config_path.write_text(json.dumps(config_data))

        config = Config(config_file=config_path)
        config.load_config()

        p = config.pilot_players[0]
        assert p.model == "test/hero-model"
        assert p.tools == ["pass_priority", "choose_action"]
        assert p.system_prompt == "Be great."


# --- deckType list (format rotation) tests ---


def _make_config_dir(tmpdir_path: Path) -> None:
    """Create minimal support files for config loading."""
    (tmpdir_path / "personalities.json").write_text("{}")
    (tmpdir_path / "models.json").write_text('{"models": []}')
    (tmpdir_path / "presets.json").write_text('{"presets": {}, "gauntlet": []}')
    (tmpdir_path / "prompts.json").write_text("{}")


def test_deck_type_list_parsed():
    """deckType as a list should populate deck_type_candidates."""
    config_data = {
        "deckType": ["Constructed - Standard", "Constructed - Modern"],
        "players": [],
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        _make_config_dir(tmpdir_path)

        config_path = tmpdir_path / "config.json"
        config_path.write_text(json.dumps(config_data))

        config = Config(config_file=config_path)
        config.load_config()
        assert config.deck_type_candidates == ["Constructed - Standard", "Constructed - Modern"]
        assert config.deck_type in config.deck_type_candidates


def test_deck_type_string_backward_compat():
    """Single string deckType should still work."""
    config_data = {
        "deckType": "Constructed - Legacy",
        "players": [],
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        _make_config_dir(tmpdir_path)

        config_path = tmpdir_path / "config.json"
        config_path.write_text(json.dumps(config_data))

        config = Config(config_file=config_path)
        config.load_config()
        assert config.deck_type == "Constructed - Legacy"
        assert config.deck_type_candidates == ["Constructed - Legacy"]


def test_deck_type_empty_list_asserts():
    """Empty deckType list should crash."""
    config_data = {"deckType": [], "players": []}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        _make_config_dir(tmpdir_path)

        config_path = tmpdir_path / "config.json"
        config_path.write_text(json.dumps(config_data))

        config = Config(config_file=config_path)
        with pytest.raises(AssertionError, match="deckType list must not be empty"):
            config.load_config()


def test_deck_type_empty_string():
    """Empty string deckType should leave deck_type_candidates empty."""
    config_data = {"deckType": "", "players": []}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        _make_config_dir(tmpdir_path)

        config_path = tmpdir_path / "config.json"
        config_path.write_text(json.dumps(config_data))

        config = Config(config_file=config_path)
        config.load_config()
        assert config.deck_type == ""
        assert config.deck_type_candidates == []


# --- models.json schema validation ---

# Fields that code actually reads from model entries.
_MODELS_JSON_FUNCTIONAL_KEYS = {
    "id",
    "name",
    "name_part",
    "ignore_providers",
    "provider_order",
    "cache_control",
    "skip_expressive_personalities",
}
# Top-level keys that code actually reads.
_MODELS_JSON_FUNCTIONAL_TOP_KEYS = {"models"}


def test_models_json_no_uncommented_fields():
    """Every non-functional field in models.json must start with '_'.

    Functional fields (read by config.py / leaderboard.py / matchmaker.py):
      model entries: id, name, name_part, ignore_providers, cache_control
      top-level: models

    Everything else is documentation and must be prefixed with '_' so it's
    obvious it's not consumed by code. This prevents accidental drift where
    someone adds a field thinking it does something.
    """
    models_path = Path(__file__).resolve().parent.parent / "models.json"
    data = json.loads(models_path.read_text())

    # Check top-level keys
    for key in data:
        if key not in _MODELS_JSON_FUNCTIONAL_TOP_KEYS and not key.startswith("_"):
            raise AssertionError(
                f"Top-level key {key!r} in models.json is not functional and must "
                f"start with '_'. Functional keys: {sorted(_MODELS_JSON_FUNCTIONAL_TOP_KEYS)}"
            )

    # Check each model entry
    for model in data.get("models", []):
        model_id = model.get("id", "???")
        for key in model:
            if key not in _MODELS_JSON_FUNCTIONAL_KEYS and not key.startswith("_"):
                raise AssertionError(
                    f"Field {key!r} on model {model_id!r} in models.json is not functional "
                    f"and must start with '_'. Functional keys: {sorted(_MODELS_JSON_FUNCTIONAL_KEYS)}"
                )


# --- Expressive personality filtering tests ---

EXPRESSIVE_PERSONALITIES = {
    "villain": {"name_part": "Villain", "prompt_suffix": "You are evil.", "expressive": True},
    "drama": {"name_part": "Drama", "prompt_suffix": "So dramatic.", "expressive": True},
    "chill": {"name_part": "Chill", "prompt_suffix": "You are chill."},
    "stoic": {"name_part": "Stoic", "prompt_suffix": "You are stoic."},
}

EXPRESSIVE_MODELS = {
    "models": [
        {"id": "test/restricted", "name": "Restricted", "name_part": "Rstrct", "skip_expressive_personalities": True},
        {"id": "test/normal", "name": "Normal", "name_part": "Normal"},
    ],
}

EXPRESSIVE_PRESETS = {
    "presets": {
        "restricted-preset": {"model": "test/restricted", "status": "active", "system_prompt": "default"},
        "normal-preset": {"model": "test/normal", "status": "active", "system_prompt": "default"},
    },
}


def test_random_personality_skips_expressive_for_restricted_model():
    """Random personality on a skip_expressive_personalities model should never pick expressive."""
    player = PilotPlayer(name="player-0", personality="random", preset="restricted-preset")
    players = [(player, False)]

    # First choice is "villain" (expressive, should be re-rolled), second is "chill" (ok)
    choices = ["villain", "chill"]
    with patch("puppeteer.config.random.choice", side_effect=choices):
        _resolve_randoms(players, EXPRESSIVE_PERSONALITIES, EXPRESSIVE_PRESETS, SAMPLE_PROMPTS, EXPRESSIVE_MODELS)

    assert player.personality == "chill"
    assert player.prompt_suffix == "You are chill."


def test_explicit_expressive_personality_allowed_on_restricted_model():
    """Explicit (non-random) expressive personality should work on restricted models."""
    player = PilotPlayer(name="player-0", personality="villain", preset="restricted-preset")
    players = [(player, False)]

    _resolve_randoms(players, EXPRESSIVE_PERSONALITIES, EXPRESSIVE_PRESETS, SAMPLE_PROMPTS, EXPRESSIVE_MODELS)

    assert player.personality == "villain"
    assert player.prompt_suffix == "You are evil."


def test_random_expressive_personality_allowed_on_normal_model():
    """Random personality on a normal model should allow expressive personalities."""
    player = PilotPlayer(name="player-0", personality="random", preset="normal-preset")
    players = [(player, False)]

    with patch("puppeteer.config.random.choice", return_value="villain"):
        _resolve_randoms(players, EXPRESSIVE_PERSONALITIES, EXPRESSIVE_PRESETS, SAMPLE_PROMPTS, EXPRESSIVE_MODELS)

    assert player.personality == "villain"
    assert player.prompt_suffix == "You are evil."
