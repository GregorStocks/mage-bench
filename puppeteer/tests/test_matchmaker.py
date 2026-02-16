"""Tests for Yente matchmaker (preset='yente' resolution)."""

import gzip
import json
from pathlib import Path

import pytest

from puppeteer.config import PilotPlayer, _resolve_randoms
from puppeteer.matchmaker import _build_key_to_preset, get_yente_pool


def _write_presets(path: Path, presets: dict, gauntlet: list[str]) -> None:
    path.write_text(json.dumps({"presets": presets, "gauntlet": gauntlet}))


def _write_models(path: Path, models: list[dict]) -> None:
    path.write_text(json.dumps({"models": models}))


def _write_game(games_dir: Path, game_id: str, game: dict) -> None:
    gz_path = games_dir / f"{game_id}.json.gz"
    gz_path.write_bytes(gzip.compress(json.dumps(game).encode()))


def _make_1v1_game(
    game_id: str,
    timestamp: str,
    winner: str,
    p1_model: str,
    p2_model: str,
    p1_effort: str | None = "medium",
    p2_effort: str | None = "medium",
    harness_epoch: int = 11,
) -> dict:
    p1: dict = {"name": "P1", "type": "pilot", "model": p1_model}
    p2: dict = {"name": "P2", "type": "pilot", "model": p2_model}
    if p1_effort:
        p1["reasoning_effort"] = p1_effort
    if p2_effort:
        p2["reasoning_effort"] = p2_effort
    return {
        "id": game_id,
        "timestamp": timestamp,
        "gameType": "Two Player Duel",
        "deckType": "Constructed - Standard",
        "winner": winner,
        "players": [p1, p2],
        "harnessEpoch": harness_epoch,
    }


def _make_commander_game(
    game_id: str,
    timestamp: str,
    winner: str,
    models: list[tuple[str, str | None]],
    harness_epoch: int = 11,
) -> dict:
    players = []
    for i, (model, effort) in enumerate(models):
        p: dict = {"name": f"P{i + 1}", "type": "pilot", "model": model}
        if effort:
            p["reasoning_effort"] = effort
        p["placement"] = 1 if f"P{i + 1}" == winner else i + 2
        players.append(p)
    return {
        "id": game_id,
        "timestamp": timestamp,
        "gameType": "",
        "deckType": "Variant Magic - Freeform Commander",
        "winner": winner,
        "players": players,
        "harnessEpoch": harness_epoch,
    }


def _setup_fixtures(tmp_path: Path, n: int = 3) -> tuple[Path, Path, Path]:
    """Create games dir, presets, and models. Returns (games_dir, presets_path, models_path)."""
    games_dir = tmp_path / "games"
    games_dir.mkdir()
    presets_path = tmp_path / "presets.json"
    models_path = tmp_path / "models.json"

    names = ["alpha", "beta", "gamma", "delta", "epsilon"][:n]
    presets = {f"{name}-medium": {"model": f"v/{name}", "reasoning_effort": "medium"} for name in names}
    gauntlet = list(presets.keys())
    _write_presets(presets_path, presets, gauntlet)
    _write_models(models_path, [{"id": f"v/{name}", "name": name.title()} for name in names])
    return games_dir, presets_path, models_path


class TestBuildKeyToPreset:
    def test_maps_gauntlet_presets(self, tmp_path: Path) -> None:
        presets_path = tmp_path / "presets.json"
        _write_presets(
            presets_path,
            {
                "a-medium": {"model": "vendor/model-a", "reasoning_effort": "medium"},
                "b-low": {"model": "vendor/model-b", "reasoning_effort": "low"},
                "c-none": {"model": "vendor/model-c"},
            },
            gauntlet=["a-medium", "b-low", "c-none"],
        )
        result = _build_key_to_preset(presets_path)
        assert result == {
            "vendor/model-a::medium": "a-medium",
            "vendor/model-b::low": "b-low",
            "vendor/model-c": "c-none",
        }

    def test_ignores_non_gauntlet_presets(self, tmp_path: Path) -> None:
        presets_path = tmp_path / "presets.json"
        _write_presets(
            presets_path,
            {
                "in-pool": {"model": "v/a", "reasoning_effort": "medium"},
                "not-in-pool": {"model": "v/b", "reasoning_effort": "medium"},
            },
            gauntlet=["in-pool"],
        )
        result = _build_key_to_preset(presets_path)
        assert "v/a::medium" in result
        assert "v/b::medium" not in result


class TestGetYentePool:
    def test_returns_presets_above_threshold(self, tmp_path: Path) -> None:
        games_dir, presets_path, models_path = _setup_fixtures(tmp_path)

        # Alpha beats Beta repeatedly -> Alpha gets high rating, Beta gets low
        for i in range(5):
            _write_game(
                games_dir,
                f"game_{i}",
                _make_1v1_game(f"game_{i}", f"2026-01-{i + 1:02d}T00:00:00Z", "P1", "v/alpha", "v/beta"),
            )

        pool = get_yente_pool(
            "Constructed - Standard",
            threshold=1650,
            games_dir=games_dir,
            presets_path=presets_path,
            models_path=models_path,
        )
        assert "alpha-medium" in pool
        assert "beta-medium" not in pool

    def test_empty_pool_when_no_games(self, tmp_path: Path) -> None:
        games_dir, presets_path, models_path = _setup_fixtures(tmp_path)
        pool = get_yente_pool(
            "Constructed - Standard",
            threshold=1600,
            games_dir=games_dir,
            presets_path=presets_path,
            models_path=models_path,
        )
        assert pool == []

    def test_commander_mode(self, tmp_path: Path) -> None:
        games_dir, presets_path, models_path = _setup_fixtures(tmp_path, n=5)

        models = [("v/alpha", "medium"), ("v/beta", "medium"), ("v/gamma", "medium"), ("v/delta", "medium")]
        _write_game(
            games_dir,
            "game_c1",
            _make_commander_game("game_c1", "2026-01-01T00:00:00Z", "P1", models),
        )

        # Empty deckType -> commander mode
        pool = get_yente_pool(
            "",
            threshold=1200,
            games_dir=games_dir,
            presets_path=presets_path,
            models_path=models_path,
        )
        # At least some models should be in the pool
        assert len(pool) > 0
        # All returned presets should be valid
        for preset in pool:
            assert preset.endswith("-medium")


class TestResolveRandomsYente:
    def test_yente_picks_from_pool(self) -> None:
        """preset='yente' should resolve to one of the presets in the yente pool."""
        player = PilotPlayer(name="test", preset="yente", personality="spike")
        presets_data = {
            "presets": {
                "a-medium": {
                    "model": "v/a",
                    "reasoning_effort": "medium",
                    "system_prompt": "default",
                    "toolset": "default",
                },
                "b-medium": {
                    "model": "v/b",
                    "reasoning_effort": "medium",
                    "system_prompt": "default",
                    "toolset": "default",
                },
            },
            "gauntlet": ["a-medium", "b-medium"],
        }
        prompts = {"default": "You are a player."}
        toolsets = {"default": ["tool1"]}
        models_data = {
            "models": [
                {"id": "v/a", "name": "Model A", "name_part": "ModA"},
                {"id": "v/b", "name": "Model B", "name_part": "ModB"},
            ]
        }
        personalities = {"spike": {"name_part": "Spike", "prompt_suffix": "Play to win."}}

        _resolve_randoms(
            [(player, True)],
            personalities,
            presets_data,
            prompts,
            models_data,
            toolsets,
            yente_pool=["a-medium", "b-medium"],
        )

        assert player.preset in ("a-medium", "b-medium")
        assert player.model is not None

    def test_yente_no_duplicates(self) -> None:
        """Two yente players should get different presets."""
        p1 = PilotPlayer(name="Player One", preset="yente", personality="spike")
        p2 = PilotPlayer(name="Player Two", preset="yente", personality="villain")
        presets_data = {
            "presets": {
                "a-medium": {
                    "model": "v/a",
                    "reasoning_effort": "medium",
                    "system_prompt": "default",
                    "toolset": "default",
                },
                "b-medium": {
                    "model": "v/b",
                    "reasoning_effort": "medium",
                    "system_prompt": "default",
                    "toolset": "default",
                },
            },
            "gauntlet": ["a-medium", "b-medium"],
        }
        prompts = {"default": "You are a player."}
        toolsets = {"default": ["tool1"]}
        models_data = {
            "models": [
                {"id": "v/a", "name": "Model A", "name_part": "ModA"},
                {"id": "v/b", "name": "Model B", "name_part": "ModB"},
            ]
        }
        personalities = {
            "spike": {"name_part": "Spike", "prompt_suffix": "Play to win."},
            "villain": {"name_part": "Vill", "prompt_suffix": "Evil."},
        }

        _resolve_randoms(
            [(p1, True), (p2, True)],
            personalities,
            presets_data,
            prompts,
            models_data,
            toolsets,
            yente_pool=["a-medium", "b-medium"],
        )

        assert p1.preset != p2.preset

    def test_yente_asserts_without_pool(self) -> None:
        """preset='yente' without a yente_pool should fail."""
        player = PilotPlayer(name="test", preset="yente", personality="spike")
        presets_data = {"presets": {}, "gauntlet": []}
        personalities = {"spike": {"name_part": "Spike", "prompt_suffix": ""}}

        with pytest.raises(AssertionError, match="yente_pool"):
            _resolve_randoms(
                [(player, True)],
                personalities,
                presets_data,
                {},
                {"models": []},
                yente_pool=None,
            )
