"""Tests for round-robin matchmaker."""

import gzip
import json
from pathlib import Path

import pytest

from puppeteer.config import PilotPlayer, _resolve_randoms
from puppeteer.matchmaker import (
    _build_key_to_preset,
    _build_matchup_matrix,
    _load_games_index,
    get_round_robin_matchup,
    pick_round_robin_format,
)


def _write_presets(path: Path, presets: dict) -> None:
    """Write presets.json — all presets get status='active' unless already set."""
    for p in presets.values():
        p.setdefault("status", "active")
    path.write_text(json.dumps({"presets": presets}))


def _write_models(path: Path, models: list[dict]) -> None:
    path.write_text(json.dumps({"models": models}))


def _write_game(games_dir: Path, game_id: str, game: dict) -> None:
    gz_path = games_dir / f"{game_id}.json5.gz"
    gz_path.write_bytes(gzip.compress(json.dumps(game).encode()))


def _write_game_json(games_dir: Path, game_id: str, game: dict) -> None:
    """Write a plain .json5 game file (not gzipped)."""
    json_path = games_dir / f"{game_id}.json5"
    json_path.write_text(json.dumps(game))


def _make_1v1_game(
    game_id: str,
    timestamp: str,
    winner: str,
    p1_model: str,
    p2_model: str,
    p1_effort: str | None = "medium",
    p2_effort: str | None = "medium",
    harness_epoch: int = 11,
    deck_type: str = "Constructed - Standard",
    season: int = 1,
) -> dict:
    p1: dict = {"name": "P1", "type": "pilot", "model": p1_model}
    p2: dict = {"name": "P2", "type": "pilot", "model": p2_model}
    if p1_effort:
        p1["reasoning_effort"] = p1_effort
    if p2_effort:
        p2["reasoning_effort"] = p2_effort
    return {
        "version": 9,
        "id": game_id,
        "timestamp": timestamp,
        "game_type": "Two Player Duel",
        "deck_type": deck_type,
        "winner": winner,
        "players": [p1, p2],
        "harness_epoch": harness_epoch,
        "season": season,
    }


def _make_commander_game(
    game_id: str,
    timestamp: str,
    winner: str,
    models: list[tuple[str, str | None]],
    harness_epoch: int = 11,
    season: int = 1,
) -> dict:
    players = []
    for i, (model, effort) in enumerate(models):
        p: dict = {"name": f"P{i + 1}", "type": "pilot", "model": model}
        if effort:
            p["reasoning_effort"] = effort
        p["placement"] = 1 if f"P{i + 1}" == winner else i + 2
        players.append(p)
    return {
        "version": 9,
        "id": game_id,
        "timestamp": timestamp,
        "game_type": "",
        "deck_type": "Variant Magic - Freeform Commander",
        "winner": winner,
        "players": players,
        "harness_epoch": harness_epoch,
        "season": season,
    }


def _setup_fixtures(tmp_path: Path, n: int = 3) -> tuple[Path, Path, Path, Path]:
    """Create games dir, presets, models, and season.json."""
    games_dir = tmp_path / "games"
    games_dir.mkdir()
    presets_path = tmp_path / "presets.json"
    models_path = tmp_path / "models.json"
    season_path = tmp_path / "season.json"

    names = ["alpha", "beta", "gamma", "delta", "epsilon"][:n]
    presets = {f"{name}-medium": {"model": f"v/{name}", "reasoning_effort": "medium"} for name in names}
    _write_presets(presets_path, presets)
    _write_models(models_path, [{"id": f"v/{name}", "name": name.title()} for name in names])
    season_path.write_text(json.dumps({"current_season": 1}))
    return games_dir, presets_path, models_path, season_path


class TestLoadGamesIndex:
    def test_loads_json_files(self, tmp_path: Path) -> None:
        games_dir = tmp_path / "games"
        games_dir.mkdir()
        game = _make_1v1_game("g1", "2026-01-01T00:00:00Z", "P1", "v/alpha", "v/beta")
        _write_game_json(games_dir, "game_1", game)
        result = _load_games_index(games_dir)
        assert len(result) == 1
        assert result[0]["id"] == "g1"

    def test_loads_gzipped_files(self, tmp_path: Path) -> None:
        games_dir = tmp_path / "games"
        games_dir.mkdir()
        game = _make_1v1_game("g2", "2026-01-01T00:00:00Z", "P1", "v/alpha", "v/beta")
        _write_game(games_dir, "game_2", game)
        result = _load_games_index(games_dir)
        assert len(result) == 1
        assert result[0]["id"] == "g2"

    def test_deduplicates_json_and_gz(self, tmp_path: Path) -> None:
        """When both .json5 and .json5.gz exist for the same game, load only once."""
        games_dir = tmp_path / "games"
        games_dir.mkdir()
        game = _make_1v1_game("g3", "2026-01-01T00:00:00Z", "P1", "v/alpha", "v/beta")
        _write_game(games_dir, "game_3", game)
        _write_game_json(games_dir, "game_3", game)
        result = _load_games_index(games_dir)
        assert len(result) == 1

    def test_loads_mixed_formats(self, tmp_path: Path) -> None:
        games_dir = tmp_path / "games"
        games_dir.mkdir()
        _write_game_json(games_dir, "game_a", _make_1v1_game("a", "2026-01-01T00:00:00Z", "P1", "v/alpha", "v/beta"))
        _write_game(games_dir, "game_b", _make_1v1_game("b", "2026-01-02T00:00:00Z", "P2", "v/alpha", "v/beta"))
        result = _load_games_index(games_dir)
        assert len(result) == 2
        ids = {g["id"] for g in result}
        assert ids == {"a", "b"}


class TestBuildKeyToPreset:
    def test_maps_active_presets(self, tmp_path: Path) -> None:
        presets_path = tmp_path / "presets.json"
        _write_presets(
            presets_path,
            {
                "a-medium": {"model": "vendor/model-a", "reasoning_effort": "medium"},
                "b-low": {"model": "vendor/model-b", "reasoning_effort": "low"},
                "c-none": {"model": "vendor/model-c"},
            },
        )
        result = _build_key_to_preset(presets_path)
        assert result == {
            "vendor/model-a::medium": "a-medium",
            "vendor/model-b::low": "b-low",
            "vendor/model-c": "c-none",
        }

    def test_ignores_inactive_presets(self, tmp_path: Path) -> None:
        presets_path = tmp_path / "presets.json"
        _write_presets(
            presets_path,
            {
                "in-pool": {"model": "v/a", "reasoning_effort": "medium"},
                "not-in-pool": {"model": "v/b", "reasoning_effort": "medium", "status": "retired"},
            },
        )
        result = _build_key_to_preset(presets_path)
        assert "v/a::medium" in result
        assert "v/b::medium" not in result


# --- Round-robin matchmaker tests ---


class TestBuildMatchupMatrix:
    def test_empty_games(self) -> None:
        pair_counts, game_counts = _build_matchup_matrix([], {})
        assert pair_counts == {}
        assert game_counts == {}

    def test_counts_1v1_pairs(self, tmp_path: Path) -> None:
        _games_dir, presets_path, _models_path, _season_path = _setup_fixtures(tmp_path)
        key_to_preset = _build_key_to_preset(presets_path)

        games = [
            _make_1v1_game("g1", "2026-01-01T00:00:00Z", "P1", "v/alpha", "v/beta"),
            _make_1v1_game("g2", "2026-01-02T00:00:00Z", "P2", "v/alpha", "v/beta"),
        ]
        pair_counts, game_counts = _build_matchup_matrix(games, key_to_preset)

        assert pair_counts[("alpha-medium", "beta-medium")] == 2
        assert game_counts["alpha-medium"] == 2
        assert game_counts["beta-medium"] == 2

    def test_counts_commander_pairs(self, tmp_path: Path) -> None:
        _games_dir, presets_path, _models_path, _season_path = _setup_fixtures(tmp_path, n=4)
        key_to_preset = _build_key_to_preset(presets_path)

        models = [("v/alpha", "medium"), ("v/beta", "medium"), ("v/gamma", "medium"), ("v/delta", "medium")]
        games = [_make_commander_game("g1", "2026-01-01T00:00:00Z", "P1", models)]
        pair_counts, _game_counts = _build_matchup_matrix(games, key_to_preset)

        # C(4,2) = 6 pairs, each with count 1
        assert len(pair_counts) == 6
        assert all(v == 1 for v in pair_counts.values())

    def test_ignores_non_active_players(self, tmp_path: Path) -> None:
        _games_dir, presets_path, _models_path, _season_path = _setup_fixtures(tmp_path)
        key_to_preset = _build_key_to_preset(presets_path)

        # "v/unknown" is not in the active pool
        game = _make_1v1_game("g1", "2026-01-01T00:00:00Z", "P1", "v/alpha", "v/unknown")
        pair_counts, game_counts = _build_matchup_matrix([game], key_to_preset)

        assert len(pair_counts) == 0  # No valid pair (one side is unknown)
        assert game_counts.get("alpha-medium") == 1
        assert "unknown" not in str(game_counts)

    def test_extra_matchups(self) -> None:
        pair_counts, game_counts = _build_matchup_matrix([], {}, extra_matchups=[("alpha-medium", "beta-medium")])
        assert pair_counts[("alpha-medium", "beta-medium")] == 1
        assert game_counts["alpha-medium"] == 1
        assert game_counts["beta-medium"] == 1


class TestGetRoundRobinMatchup:
    def test_zero_games_returns_valid_group(self, tmp_path: Path) -> None:
        """With no history, any valid group is acceptable."""
        games_dir, presets_path, models_path, season_path = _setup_fixtures(tmp_path)
        picks = get_round_robin_matchup(
            "Constructed - Standard",
            2,
            games_dir=games_dir,
            presets_path=presets_path,
            models_path=models_path,
            season_path=season_path,
        )
        assert len(picks) == 2
        assert picks[0] != picks[1]
        active = {"alpha-medium", "beta-medium", "gamma-medium"}
        assert set(picks).issubset(active)

    def test_prefers_unplayed_pair(self, tmp_path: Path) -> None:
        """Should prefer the pair that has never played each other."""
        games_dir, presets_path, models_path, season_path = _setup_fixtures(tmp_path)

        # Alpha vs Beta played 5 times, gamma untouched
        for i in range(5):
            _write_game(
                games_dir,
                f"game_{i}",
                _make_1v1_game(f"game_{i}", f"2026-01-{i + 1:02d}T00:00:00Z", "P1", "v/alpha", "v/beta"),
            )

        picks = get_round_robin_matchup(
            "Constructed - Standard",
            2,
            games_dir=games_dir,
            presets_path=presets_path,
            models_path=models_path,
            season_path=season_path,
        )
        picked = set(picks)
        # Should include gamma (untouched) paired with either alpha or beta
        assert "gamma-medium" in picked

    def test_tiebreaks_by_games_played(self, tmp_path: Path) -> None:
        """Among pairs with equal matchup count, prefer models with fewer games."""
        games_dir, presets_path, models_path, season_path = _setup_fixtures(tmp_path, n=4)

        # alpha-beta: 1 game, alpha-gamma: 1 game
        # Unplayed pairs: alpha-delta, beta-gamma, beta-delta, gamma-delta
        # Among those, delta has 0 games, gamma has 1, alpha has 2, beta has 1
        # Best pair by games: beta-delta (1+0=1) or gamma-delta (1+0=1)
        _write_game(
            games_dir,
            "game_1",
            _make_1v1_game("game_1", "2026-01-01T00:00:00Z", "P1", "v/alpha", "v/beta"),
        )
        _write_game(
            games_dir,
            "game_2",
            _make_1v1_game("game_2", "2026-01-02T00:00:00Z", "P1", "v/alpha", "v/gamma"),
        )

        picks = get_round_robin_matchup(
            "Constructed - Standard",
            2,
            games_dir=games_dir,
            presets_path=presets_path,
            models_path=models_path,
            season_path=season_path,
        )
        picked = set(picks)
        # delta (0 games) should be in the pick
        assert "delta-medium" in picked
        # Paired with beta or gamma (1 game each), not alpha (2 games)
        assert "alpha-medium" not in picked

    def test_commander_four_seats(self, tmp_path: Path) -> None:
        games_dir, presets_path, models_path, season_path = _setup_fixtures(tmp_path, n=5)
        picks = get_round_robin_matchup(
            "",
            4,
            games_dir=games_dir,
            presets_path=presets_path,
            models_path=models_path,
            season_path=season_path,
        )
        assert len(picks) == 4
        assert len(set(picks)) == 4  # All unique

    def test_filters_by_format(self, tmp_path: Path) -> None:
        """1v1 matchmaker should only count 1v1 games, not commander."""
        games_dir, presets_path, models_path, season_path = _setup_fixtures(tmp_path, n=4)

        # Alpha-beta played in commander (should be ignored for 1v1)
        models = [("v/alpha", "medium"), ("v/beta", "medium"), ("v/gamma", "medium"), ("v/delta", "medium")]
        _write_game(
            games_dir,
            "game_cmdr",
            _make_commander_game("game_cmdr", "2026-01-01T00:00:00Z", "P1", models),
        )
        # Alpha-gamma played in 1v1 (should be counted)
        _write_game(
            games_dir,
            "game_1v1",
            _make_1v1_game("game_1v1", "2026-01-02T00:00:00Z", "P1", "v/alpha", "v/gamma"),
        )

        picks = get_round_robin_matchup(
            "Constructed - Standard",
            2,
            games_dir=games_dir,
            presets_path=presets_path,
            models_path=models_path,
            season_path=season_path,
        )
        picked = set(picks)
        # Commander game ignored for 1v1. Only alpha-gamma counts.
        # Unplayed 1v1 pairs with 0 count: alpha-beta, alpha-delta, beta-gamma, beta-delta, gamma-delta
        # delta has 0 games total, so it should appear
        assert "delta-medium" in picked

    def test_filters_by_season(self, tmp_path: Path) -> None:
        """Only games from the current season are counted."""
        games_dir, presets_path, models_path, season_path = _setup_fixtures(tmp_path)
        # Current season is 1 (set in _setup_fixtures)

        # Write a season-0 game (pre-season) — should be ignored
        _write_game(
            games_dir,
            "game_old",
            _make_1v1_game("game_old", "2026-01-01T00:00:00Z", "P1", "v/alpha", "v/beta", season=0),
        )
        # Write a season-2 game (future season) — should also be ignored
        _write_game(
            games_dir,
            "game_future",
            _make_1v1_game("game_future", "2026-03-01T00:00:00Z", "P1", "v/alpha", "v/gamma", season=2),
        )
        # Write a current-season game — alpha-gamma is played
        _write_game(
            games_dir,
            "game_current",
            _make_1v1_game("game_current", "2026-01-02T00:00:00Z", "P1", "v/alpha", "v/gamma", season=1),
        )

        picks = get_round_robin_matchup(
            "Constructed - Standard",
            2,
            games_dir=games_dir,
            presets_path=presets_path,
            models_path=models_path,
            season_path=season_path,
        )
        picked = set(picks)
        # Only the season-1 alpha-gamma game counts.
        # Unplayed pairs: alpha-beta, beta-gamma. beta has 0 games, so
        # alpha-beta (2+0=2) or beta-gamma (0+1=1) are candidates.
        # beta-gamma has lower games_score, so it should be picked.
        assert "beta-medium" in picked

    def test_extra_matchups_shifts_selection(self, tmp_path: Path) -> None:
        """extra_matchups from parallel batch should prevent duplicate selections."""
        games_dir, presets_path, models_path, season_path = _setup_fixtures(tmp_path)

        # No historical games. First pick could be anything.
        # With extra_matchups claiming alpha-beta, should pick a different pair.
        picks = get_round_robin_matchup(
            "Constructed - Standard",
            2,
            games_dir=games_dir,
            presets_path=presets_path,
            models_path=models_path,
            extra_matchups=[("alpha-medium", "beta-medium")],
            season_path=season_path,
        )
        picked = set(picks)
        # Should NOT be alpha-beta (already "played" in this batch)
        assert picked != {"alpha-medium", "beta-medium"}


class TestResolveRandomsRoundRobin:
    def _make_fixtures(self) -> tuple[dict, dict, dict, dict, dict]:
        presets_data = {
            "presets": {
                "a-medium": {
                    "model": "v/a",
                    "status": "active",
                    "reasoning_effort": "medium",
                    "system_prompt": "default",
                    "toolset": "default",
                },
                "b-medium": {
                    "model": "v/b",
                    "status": "active",
                    "reasoning_effort": "medium",
                    "system_prompt": "default",
                    "toolset": "default",
                },
            },
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
        return presets_data, prompts, toolsets, models_data, personalities

    def test_round_robin_picks_in_order(self) -> None:
        """preset='round-robin' should consume picks in order."""
        presets_data, prompts, toolsets, models_data, personalities = self._make_fixtures()

        p1 = PilotPlayer(name="Player One", preset="round-robin", personality="spike")
        p2 = PilotPlayer(name="Player Two", preset="round-robin", personality="villain")

        _resolve_randoms(
            [(p1, True), (p2, True)],
            personalities,
            presets_data,
            prompts,
            models_data,
            toolsets,
            round_robin_picks=["a-medium", "b-medium"],
        )

        assert p1.preset == "a-medium"
        assert p2.preset == "b-medium"
        assert p1.model == "v/a"
        assert p2.model == "v/b"

    def test_round_robin_asserts_without_picks(self) -> None:
        """preset='round-robin' without round_robin_picks should fail."""
        player = PilotPlayer(name="test", preset="round-robin", personality="spike")
        presets_data = {"presets": {}}
        personalities = {"spike": {"name_part": "Spike", "prompt_suffix": ""}}

        with pytest.raises(AssertionError, match="round_robin_picks"):
            _resolve_randoms(
                [(player, True)],
                personalities,
                presets_data,
                {},
                {"models": []},
                round_robin_picks=None,
            )

    def test_round_robin_asserts_insufficient_picks(self) -> None:
        """More round-robin players than picks should fail."""
        presets_data, prompts, toolsets, models_data, personalities = self._make_fixtures()

        p1 = PilotPlayer(name="PlayerOne", preset="round-robin", personality="spike")
        p2 = PilotPlayer(name="PlayerTwo", preset="round-robin", personality="villain")

        with pytest.raises((AssertionError, IndexError)):
            _resolve_randoms(
                [(p1, True), (p2, True)],
                personalities,
                presets_data,
                prompts,
                models_data,
                toolsets,
                round_robin_picks=["a-medium"],  # Only 1 pick for 2 players
            )


class TestPickRoundRobinFormat:
    def test_picks_least_played_format(self, tmp_path: Path) -> None:
        """Should pick the format where the selected bots have fewest games."""
        games_dir, presets_path, _models_path, season_path = _setup_fixtures(tmp_path)

        # alpha and beta have 3 Standard games, 0 Modern games
        for i in range(3):
            _write_game(
                games_dir,
                f"game_s{i}",
                _make_1v1_game(
                    f"game_s{i}",
                    f"2026-01-{i + 1:02d}T00:00:00Z",
                    "P1",
                    "v/alpha",
                    "v/beta",
                    deck_type="Constructed - Standard",
                ),
            )

        candidates = ["Constructed - Standard", "Constructed - Modern"]
        chosen = pick_round_robin_format(
            candidates,
            ["alpha-medium", "beta-medium"],
            games_dir=games_dir,
            presets_path=presets_path,
            season_path=season_path,
        )
        assert chosen == "Constructed - Modern"

    def test_equal_counts_picks_any(self, tmp_path: Path) -> None:
        """When all formats have equal counts, any is valid."""
        games_dir, presets_path, _models_path, season_path = _setup_fixtures(tmp_path)

        candidates = ["Constructed - Standard", "Constructed - Modern", "Constructed - Legacy"]
        chosen = pick_round_robin_format(
            candidates,
            ["alpha-medium", "beta-medium"],
            games_dir=games_dir,
            presets_path=presets_path,
            season_path=season_path,
        )
        assert chosen in candidates

    def test_extra_format_picks_shifts_selection(self, tmp_path: Path) -> None:
        """Parallel batch coordination: earlier picks should shift selection."""
        games_dir, presets_path, _models_path, season_path = _setup_fixtures(tmp_path)

        candidates = ["Constructed - Standard", "Constructed - Modern"]
        chosen = pick_round_robin_format(
            candidates,
            ["alpha-medium", "beta-medium"],
            games_dir=games_dir,
            presets_path=presets_path,
            extra_format_picks=["Constructed - Standard"],
            season_path=season_path,
        )
        assert chosen == "Constructed - Modern"

    def test_single_candidate_asserts(self, tmp_path: Path) -> None:
        """Single candidate should assert."""
        games_dir, presets_path, _models_path, season_path = _setup_fixtures(tmp_path)

        with pytest.raises(AssertionError, match="multiple candidates"):
            pick_round_robin_format(
                ["Constructed - Standard"],
                ["alpha-medium"],
                games_dir=games_dir,
                presets_path=presets_path,
                season_path=season_path,
            )

    def test_balances_per_bot(self, tmp_path: Path) -> None:
        """Should consider per-bot counts, not just global totals."""
        games_dir, presets_path, _models_path, season_path = _setup_fixtures(tmp_path)

        # alpha has 2 Standard games (with beta), 0 Modern
        # gamma has 0 Standard, 2 Modern (with beta)
        # When picking for alpha+gamma, Standard score=2, Modern score=2 -> tied
        # When picking for alpha+beta, Standard score=4, Modern score=2 -> Modern
        for i in range(2):
            _write_game(
                games_dir,
                f"game_s{i}",
                _make_1v1_game(
                    f"game_s{i}",
                    f"2026-01-{i + 1:02d}T00:00:00Z",
                    "P1",
                    "v/alpha",
                    "v/beta",
                    deck_type="Constructed - Standard",
                ),
            )
            _write_game(
                games_dir,
                f"game_m{i}",
                _make_1v1_game(
                    f"game_m{i}",
                    f"2026-02-{i + 1:02d}T00:00:00Z",
                    "P1",
                    "v/gamma",
                    "v/beta",
                    deck_type="Constructed - Modern",
                ),
            )

        candidates = ["Constructed - Standard", "Constructed - Modern"]
        chosen = pick_round_robin_format(
            candidates,
            ["alpha-medium", "beta-medium"],
            games_dir=games_dir,
            presets_path=presets_path,
            season_path=season_path,
        )
        assert chosen == "Constructed - Modern"

    def test_ignores_other_season_games(self, tmp_path: Path) -> None:
        """Only current-season games should affect format selection."""
        games_dir, presets_path, _models_path, season_path = _setup_fixtures(tmp_path)

        # Pre-season game in Modern (should be ignored)
        _write_game(
            games_dir,
            "game_old",
            _make_1v1_game(
                "game_old",
                "2026-01-01T00:00:00Z",
                "P1",
                "v/alpha",
                "v/beta",
                season=0,
                deck_type="Constructed - Modern",
            ),
        )
        # Current epoch game in Standard
        _write_game(
            games_dir,
            "game_new",
            _make_1v1_game(
                "game_new",
                "2026-01-02T00:00:00Z",
                "P1",
                "v/alpha",
                "v/beta",
                deck_type="Constructed - Standard",
            ),
        )

        candidates = ["Constructed - Standard", "Constructed - Modern"]
        chosen = pick_round_robin_format(
            candidates,
            ["alpha-medium", "beta-medium"],
            games_dir=games_dir,
            presets_path=presets_path,
            season_path=season_path,
        )
        # Old Modern game ignored, only Standard game counts -> Modern wins
        assert chosen == "Constructed - Modern"
