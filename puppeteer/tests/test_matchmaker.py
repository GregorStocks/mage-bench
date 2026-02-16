"""Tests for matchmaker (Yente) config generation."""

import gzip
import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "matchmaker.py"
_spec = importlib.util.spec_from_file_location("matchmaker", _SCRIPT)
assert _spec is not None and _spec.loader is not None
matchmaker_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(matchmaker_mod)

_build_key_to_preset = matchmaker_mod._build_key_to_preset
_matchmake = matchmaker_mod.matchmake


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
    deck_type: str = "Constructed - Standard",
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
        "deckType": deck_type,
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
        if f"P{i + 1}" == winner:
            p["placement"] = 1
        else:
            p["placement"] = i + 2
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


def _setup_fixtures(tmp_path: Path, n: int = 3) -> tuple[Path, Path, Path]:
    """Create games, presets, and models fixtures. Returns (games_dir, presets, models)."""
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


class TestMatchmake1v1:
    def test_picks_two_above_threshold(self, tmp_path: Path) -> None:
        games_dir, presets_path, models_path = _setup_fixtures(tmp_path)

        # Alpha beats Beta and Gamma repeatedly -> Alpha gets high rating
        for i in range(5):
            _write_game(
                games_dir,
                f"game_a_vs_b_{i}",
                _make_1v1_game(f"game_a_vs_b_{i}", f"2026-01-{i + 1:02d}T00:00:00Z", "P1", "v/alpha", "v/beta"),
            )
            _write_game(
                games_dir,
                f"game_a_vs_g_{i}",
                _make_1v1_game(f"game_a_vs_g_{i}", f"2026-01-{i + 10:02d}T00:00:00Z", "P1", "v/alpha", "v/gamma"),
            )

        # With a high threshold only alpha qualifies -> should fail
        with pytest.raises(ValueError, match="Need at least 2"):
            _matchmake(
                mode="1v1",
                games_dir=games_dir,
                presets_path=presets_path,
                models_path=models_path,
                threshold=1700,
                format_name="standard",
            )

    def test_generates_valid_config(self, tmp_path: Path) -> None:
        games_dir, presets_path, models_path = _setup_fixtures(tmp_path)

        _write_game(
            games_dir,
            "game_1",
            _make_1v1_game("game_1", "2026-01-01T00:00:00Z", "P1", "v/alpha", "v/beta"),
        )
        _write_game(
            games_dir,
            "game_2",
            _make_1v1_game("game_2", "2026-01-02T00:00:00Z", "P1", "v/gamma", "v/beta"),
        )

        config = _matchmake(
            mode="1v1",
            games_dir=games_dir,
            presets_path=presets_path,
            models_path=models_path,
            threshold=1580,
            format_name="modern",
        )

        assert config["gameType"] == "Two Player Duel"
        assert config["deckType"] == "Constructed - Modern"
        assert len(config["players"]) == 2
        for p in config["players"]:
            assert p["type"] == "pilot"
            assert p["preset"] in ("alpha-medium", "beta-medium", "gamma-medium")
            assert p["personality"] == "random"
            assert p["deck"] == "random"
        assert config["players"][0]["preset"] != config["players"][1]["preset"]

    def test_format_random_when_not_specified(self, tmp_path: Path) -> None:
        games_dir, presets_path, models_path = _setup_fixtures(tmp_path)
        _write_game(
            games_dir,
            "game_1",
            _make_1v1_game("game_1", "2026-01-01T00:00:00Z", "P1", "v/alpha", "v/beta"),
        )

        config = _matchmake(
            mode="1v1",
            games_dir=games_dir,
            presets_path=presets_path,
            models_path=models_path,
            threshold=1550,
            format_name=None,
        )
        assert config["deckType"] in (
            "Constructed - Standard",
            "Constructed - Modern",
            "Constructed - Legacy",
        )

    def test_error_when_no_games(self, tmp_path: Path) -> None:
        games_dir, presets_path, models_path = _setup_fixtures(tmp_path)
        with pytest.raises(ValueError, match="Only 0 model"):
            _matchmake(
                mode="1v1",
                games_dir=games_dir,
                presets_path=presets_path,
                models_path=models_path,
                threshold=1600,
                format_name="standard",
            )


class TestMatchmakeCommander:
    def test_picks_four_models(self, tmp_path: Path) -> None:
        games_dir, presets_path, models_path = _setup_fixtures(tmp_path, n=5)

        models = [("v/alpha", "medium"), ("v/beta", "medium"), ("v/gamma", "medium"), ("v/delta", "medium")]
        _write_game(
            games_dir,
            "game_c1",
            _make_commander_game("game_c1", "2026-01-01T00:00:00Z", "P1", models),
        )
        _write_game(
            games_dir,
            "game_c2",
            _make_commander_game(
                "game_c2",
                "2026-01-02T00:00:00Z",
                "P2",
                [("v/beta", "medium"), ("v/epsilon", "medium"), ("v/alpha", "medium"), ("v/gamma", "medium")],
            ),
        )

        config = _matchmake(
            mode="commander",
            games_dir=games_dir,
            presets_path=presets_path,
            models_path=models_path,
            threshold=1200,
        )

        assert "gameType" not in config
        assert "deckType" not in config
        assert len(config["players"]) == 4
        presets_used = {p["preset"] for p in config["players"]}
        assert len(presets_used) == 4

    def test_error_when_fewer_than_four(self, tmp_path: Path) -> None:
        games_dir, presets_path, models_path = _setup_fixtures(tmp_path, n=3)

        models = [("v/alpha", "medium"), ("v/beta", "medium"), ("v/gamma", "medium"), ("v/alpha", "medium")]
        _write_game(
            games_dir,
            "game_c1",
            _make_commander_game("game_c1", "2026-01-01T00:00:00Z", "P1", models),
        )

        # Only 3 models exist, need 4 -> should fail at high threshold
        with pytest.raises(ValueError, match="Need at least 4"):
            _matchmake(
                mode="commander",
                games_dir=games_dir,
                presets_path=presets_path,
                models_path=models_path,
                threshold=1600,
            )

    def test_commander_ignores_1v1_games(self, tmp_path: Path) -> None:
        games_dir, presets_path, models_path = _setup_fixtures(tmp_path, n=5)

        # Only 1v1 games -> commander pool has no ratings
        _write_game(
            games_dir,
            "game_1",
            _make_1v1_game("game_1", "2026-01-01T00:00:00Z", "P1", "v/alpha", "v/beta"),
        )

        with pytest.raises(ValueError, match=r"Only 0 model.*commander"):
            _matchmake(
                mode="commander",
                games_dir=games_dir,
                presets_path=presets_path,
                models_path=models_path,
                threshold=1500,
            )
