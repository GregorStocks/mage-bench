"""Tests for tournament game runner.

Unit tests for bracket generation, round naming, next-match finding,
result recording, and deck file writing.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import magebench.cli.tournament_game as tournament_game

# -- Bracket generation --


class TestGenerateBracket:
    def test_size_2(self):
        assert tournament_game.generate_bracket(2) == [(1, 2)]

    def test_size_4(self):
        bracket = tournament_game.generate_bracket(4)
        assert bracket == [(1, 4), (2, 3)]
        # Seeds 1 and 2 are on opposite sides
        assert bracket[0][0] == 1
        assert bracket[1][0] == 2

    def test_size_8(self):
        bracket = tournament_game.generate_bracket(8)
        assert bracket == [(1, 8), (4, 5), (2, 7), (3, 6)]
        # Top half: 1v8, 4v5; bottom half: 2v7, 3v6
        # 1 and 2 can only meet in the final
        top_seeds = {s for pair in bracket[:2] for s in pair}
        bottom_seeds = {s for pair in bracket[2:] for s in pair}
        assert 1 in top_seeds and 2 in bottom_seeds

    def test_size_16(self):
        bracket = tournament_game.generate_bracket(16)
        assert len(bracket) == 8
        # All seeds 1-16 appear exactly once
        all_seeds = [s for pair in bracket for s in pair]
        assert sorted(all_seeds) == list(range(1, 17))
        # Each match pairs a high seed with a low seed (sum = 17)
        for a, b in bracket:
            assert a + b == 17

    def test_non_power_of_2_fails(self):
        with pytest.raises(AssertionError, match="power of 2"):
            tournament_game.generate_bracket(6)

    def test_size_1_fails(self):
        with pytest.raises(AssertionError, match="power of 2"):
            tournament_game.generate_bracket(1)


# -- Round naming --


class TestRoundName:
    def test_finals(self):
        assert tournament_game.round_name(1) == "Finals"

    def test_semifinals(self):
        assert tournament_game.round_name(2) == "Semifinals"

    def test_quarterfinals(self):
        assert tournament_game.round_name(4) == "Quarterfinals"

    def test_round_of_16(self):
        assert tournament_game.round_name(8) == "Round of 16"


# -- Finding next match --


def _make_tournament(size: int, rounds: list | None = None) -> dict:
    """Create a minimal tournament dict for testing."""
    return {
        "size": size,
        "entrants": [{"seed": i + 1} for i in range(size)],
        "rounds": rounds or [],
    }


def _make_finished_tournament(size: int = 4) -> dict:
    """Create a completed tournament where the higher seed always wins."""
    tournament = {
        "season": 1,
        "size": size,
        "best_of": 3,
        "entrants": [{"seed": i + 1, "display_name": f"Seed {i + 1}"} for i in range(size)],
        "rounds": [],
    }
    game_num = 1
    while True:
        result = tournament_game.find_next_match(tournament)
        if result is None:
            break
        _, match = result
        winner = min(match["seed_a"], match["seed_b"])
        match["winner_seed"] = winner
        match["games"].append({"game_id": f"game_{game_num}", "winner_seed": winner})
        game_num += 1
    return tournament


class TestFindNextMatch:
    def test_empty_rounds_generates_round_1(self):
        t = _make_tournament(8)
        result = tournament_game.find_next_match(t)
        assert result is not None
        round_dict, match = result
        assert round_dict["round"] == 1
        assert round_dict["name"] == "Quarterfinals"
        assert len(round_dict["matches"]) == 4
        assert match["seed_a"] == 1
        assert match["seed_b"] == 8
        assert match["winner_seed"] is None
        # All rounds pre-generated (QF + SF + Finals for 8 players)
        assert len(t["rounds"]) == 3

    def test_partially_complete_round(self):
        t = _make_tournament(4)
        # Generate round 1 first
        tournament_game.find_next_match(t)
        # Complete first match
        t["rounds"][0]["matches"][0]["winner_seed"] = 1
        # Should return second match
        result = tournament_game.find_next_match(t)
        assert result is not None
        _, match = result
        assert match["match"] == 2
        assert match["seed_a"] == 2
        assert match["seed_b"] == 3

    def test_complete_round_generates_next(self):
        t = _make_tournament(4)
        tournament_game.find_next_match(t)
        # Complete both round 1 matches
        t["rounds"][0]["matches"][0]["winner_seed"] = 1  # 1 beats 4
        t["rounds"][0]["matches"][1]["winner_seed"] = 2  # 2 beats 3
        # Should generate round 2 (finals)
        result = tournament_game.find_next_match(t)
        assert result is not None
        round_dict, match = result
        assert round_dict["round"] == 2
        assert round_dict["name"] == "Finals"
        assert match["seed_a"] == 1
        assert match["seed_b"] == 2

    def test_tournament_complete(self):
        t = _make_tournament(2)
        tournament_game.find_next_match(t)
        # Complete the only match
        t["rounds"][0]["matches"][0]["winner_seed"] = 1
        result = tournament_game.find_next_match(t)
        assert result is None

    def test_8_player_full_bracket(self):
        t = _make_tournament(8)
        # Play through all 7 matches
        matches_played = 0
        while True:
            result = tournament_game.find_next_match(t)
            if result is None:
                break
            _, match = result
            # Higher seed (lower number) always wins
            winner = min(match["seed_a"], match["seed_b"])
            match["winner_seed"] = winner
            match["games"].append({"game_id": f"game_{matches_played}", "winner_seed": winner})
            matches_played += 1
        assert matches_played == 7
        # Seed 1 wins the tournament
        final = t["rounds"][-1]["matches"][0]
        assert final["winner_seed"] == 1


def test_get_tournament_champion_seed_requires_complete_bracket():
    tournament = _make_tournament(4)
    tournament_game.find_next_match(tournament)
    assert tournament_game.get_tournament_champion_seed(tournament) is None


def test_run_match_crowns_champion_and_enters_between_seasons(monkeypatch, tmp_path: Path, capsys):
    tournament = _make_finished_tournament()
    data_dir = tmp_path / "data"
    tournaments_dir = data_dir / "tournaments"
    tournaments_dir.mkdir(parents=True)
    tournament_path = tournaments_dir / "season-1.json"
    tournament_path.write_text(json.dumps(tournament, indent=2) + "\n")
    season_file = data_dir / "season.json"
    season_file.write_text(
        json.dumps(
            {
                "current_season": 1,
                "phase": tournament_game.TOURNAMENT_PHASE,
                "tournament": "data/tournaments/season-1.json",
            },
            indent=2,
        )
        + "\n"
    )

    monkeypatch.setattr(tournament_game, "_ROOT", tmp_path)
    monkeypatch.setattr(tournament_game, "_SEASON_FILE", season_file)

    assert tournament_game.run_match(tournament, tournament_path) is False

    saved_tournament = json.loads(tournament_path.read_text())
    assert saved_tournament["champion_seed"] == 1
    assert saved_tournament["completed_at"]

    saved_season = json.loads(season_file.read_text())
    assert saved_season["current_season"] == 1
    assert saved_season["phase"] == tournament_game.BETWEEN_SEASONS_PHASE
    assert saved_season["tournament"] == "data/tournaments/season-1.json"

    output = capsys.readouterr().out
    assert "run 'make conclude-tournament'" in output
    assert "Tournament is complete! Champion: #1 Seed 1" in output


# -- Finding ready matches (parallel support) --


class TestFindReadyMatches:
    def test_empty_rounds_returns_all_first_round(self):
        t = _make_tournament(8)
        ready = tournament_game.find_ready_matches(t)
        assert len(ready) == 4
        seeds = [(r[1]["seed_a"], r[1]["seed_b"]) for r in ready]
        assert (1, 8) in seeds
        assert (4, 5) in seeds
        assert (2, 7) in seeds
        assert (3, 6) in seeds

    def test_partially_complete_round(self):
        t = _make_tournament(4)
        tournament_game.find_ready_matches(t)
        t["rounds"][0]["matches"][0]["winner_seed"] = 1
        ready = tournament_game.find_ready_matches(t)
        assert len(ready) == 1
        assert ready[0][1]["seed_a"] == 2
        assert ready[0][1]["seed_b"] == 3

    def test_round_complete_advances_to_next(self):
        t = _make_tournament(4)
        tournament_game.find_ready_matches(t)
        t["rounds"][0]["matches"][0]["winner_seed"] = 1
        t["rounds"][0]["matches"][1]["winner_seed"] = 2
        ready = tournament_game.find_ready_matches(t)
        assert len(ready) == 1
        assert ready[0][1]["seed_a"] == 1
        assert ready[0][1]["seed_b"] == 2

    def test_tournament_complete_returns_empty(self):
        t = _make_tournament(2)
        tournament_game.find_ready_matches(t)
        t["rounds"][0]["matches"][0]["winner_seed"] = 1
        ready = tournament_game.find_ready_matches(t)
        assert len(ready) == 0

    def test_all_round_matches_returned(self):
        """All 8 first-round matches in a 16-player bracket are returned."""
        t = _make_tournament(16)
        ready = tournament_game.find_ready_matches(t)
        assert len(ready) == 8

    def test_consistent_with_find_next_match(self):
        """First element of find_ready_matches matches find_next_match."""
        t = _make_tournament(8)
        ready = tournament_game.find_ready_matches(t)
        single = tournament_game.find_next_match(t)
        assert single is not None
        assert ready[0][1] is single[1]


# -- Deck file writing --


class TestWriteTournamentDeck:
    def test_writes_dck_file(self, tmp_path: Path):
        cards = ["1 [JMP:1] Serra Angel", "7 [JMP:2] Plains"]
        half_decks = ["Angels", "Cats"]
        rel_path = tournament_game.write_tournament_deck(tmp_path, 3, cards, half_decks)
        full_path = tmp_path / rel_path
        assert full_path.exists()
        content = full_path.read_text()
        assert "NAME:Angels + Cats" in content
        assert "1 [JMP:1] Serra Angel" in content
        assert "7 [JMP:2] Plains" in content

    def test_filename_contains_seed(self, tmp_path: Path):
        rel_path = tournament_game.write_tournament_deck(tmp_path, 5, ["1 [JMP:1] Card"], ["Pack"])
        assert "seed-5" in str(rel_path)

    def test_path_is_relative(self, tmp_path: Path):
        rel_path = tournament_game.write_tournament_deck(tmp_path, 1, ["1 [JMP:1] Card"], ["Pack"])
        assert not rel_path.is_absolute()


def test_load_match_wins_resumes_partial_series():
    """Recorded games should count toward the current best-of score."""
    match = {
        "seed_a": 1,
        "seed_b": 4,
        "games": [
            {"game_id": "g1", "winner_seed": 1},
            {"game_id": "g2", "winner_seed": 4},
            {"game_id": "g3", "winner_seed": 1},
        ],
    }
    assert tournament_game._load_match_wins(match, 1, 4) == {1: 2, 4: 1}


def test_make_runner_config_for_batch(tmp_path: Path):
    """Batch runner config should map one config file per game."""
    config_a = tmp_path / "a.json"
    config_b = tmp_path / "b.json"
    config_a.write_text("{}\n")
    config_b.write_text("{}\n")

    config = tournament_game._make_runner_config(
        [config_a, config_b],
        skip_compile=True,
    )

    assert config.config_file == config_a
    assert config.batch_config_files == [config_a, config_b]
    assert config.num_games == 2
    assert config.skip_compile is True
    assert config.observer is True
    assert config.record is True


def test_run_games_reads_winners_in_session_order(monkeypatch):
    """Shared game runner should map each finished game back to its matchup."""

    def fake_run_orchestrator(config, project_root):
        _ = config, project_root
        return MagicMock(
            exit_code=0,
            sessions=[
                MagicMock(game_dir=Path("/tmp/game_1")),
                MagicMock(game_dir=Path("/tmp/game_2")),
            ],
        )

    monkeypatch.setattr(
        tournament_game,
        "build_game_config",
        lambda _tournament, seed_a, seed_b, _root: Path(f"/tmp/{seed_a}-vs-{seed_b}.json"),
    )
    monkeypatch.setattr(tournament_game, "run_orchestrator", fake_run_orchestrator)
    monkeypatch.setattr(
        tournament_game,
        "read_game_winner",
        lambda game_dir: "alice" if game_dir.name == "game_1" else "bob",
    )
    monkeypatch.setattr(
        tournament_game,
        "map_winner_to_seed",
        lambda winner_name, seed_a, seed_b, _tournament: seed_a if winner_name == "alice" else seed_b,
    )

    results = tournament_game._run_games(
        {"entrants": []},
        [(1, 8), (2, 7)],
        skip_compile=True,
    )

    assert results == [
        (Path("/tmp/game_1"), 1),
        (Path("/tmp/game_2"), 7),
    ]


def test_record_match_game_updates_series_and_finishes_match(monkeypatch, tmp_path: Path):
    tournament = {
        "entrants": [
            {"seed": 1, "display_name": "Alpha"},
            {"seed": 4, "display_name": "Beta"},
        ]
    }
    match = {
        "seed_a": 1,
        "seed_b": 4,
        "winner_seed": None,
        "games": [{"game_id": "existing", "winner_seed": 1}],
    }
    wins = {1: 1, 4: 0}
    tournament_path = tmp_path / "tournament.json"
    tournament_path.write_text("{}\n")
    uploads: list[Path] = []

    monkeypatch.setattr(
        tournament_game,
        "upload_and_export",
        lambda game_dir, *_args, **_kwargs: uploads.append(game_dir),
    )

    finished = tournament_game._record_match_game(
        tournament,
        tournament_path,
        match,
        wins,
        2,
        {1: {"display_name": "Alpha"}, 4: {"display_name": "Beta"}},
        Path("/tmp/game_2"),
        1,
        "Game 2",
    )

    assert finished is True
    assert wins == {1: 2, 4: 0}
    assert match["winner_seed"] == 1
    assert [game["winner_seed"] for game in match["games"]] == [1, 1]
    assert uploads == [Path("/tmp/game_2")]


def test_run_match_on_resumes_partial_series(monkeypatch, tmp_path: Path):
    """A resumed match should continue from existing recorded wins."""
    tournament = {
        "best_of": 3,
        "entrants": [
            {"seed": 1, "display_name": "Alpha"},
            {"seed": 4, "display_name": "Beta"},
        ],
    }
    match = {
        "match": 1,
        "seed_a": 1,
        "seed_b": 4,
        "winner_seed": None,
        "games": [{"game_id": "existing", "winner_seed": 1}],
    }
    tournament_path = tmp_path / "tournament.json"
    tournament_path.write_text("{}\n")

    monkeypatch.setattr(
        tournament_game,
        "_run_games",
        lambda *_args, **_kwargs: [(Path("/tmp/game_2"), 1)],
    )
    monkeypatch.setattr(tournament_game, "upload_and_export", lambda *_args, **_kwargs: None)

    tournament_game._run_match_on(
        tournament,
        tournament_path,
        {"round": 1, "name": "Semifinals"},
        match,
        skip_compile=True,
    )

    assert match["winner_seed"] == 1
    assert [game["winner_seed"] for game in match["games"]] == [1, 1]


def test_run_match_batch_plays_each_series_until_decided(monkeypatch, tmp_path: Path):
    tournament = {
        "best_of": 3,
        "entrants": [
            {"seed": 1, "display_name": "Alpha"},
            {"seed": 4, "display_name": "Beta"},
            {"seed": 2, "display_name": "Gamma"},
            {"seed": 3, "display_name": "Delta"},
        ],
    }
    batch = [
        (
            {"round": 1, "name": "Semifinals"},
            {"match": 1, "seed_a": 1, "seed_b": 4, "winner_seed": None, "games": []},
        ),
        (
            {"round": 1, "name": "Semifinals"},
            {"match": 2, "seed_a": 2, "seed_b": 3, "winner_seed": None, "games": []},
        ),
    ]
    tournament_path = tmp_path / "tournament.json"
    tournament_path.write_text("{}\n")
    results_by_call = [
        [(Path("/tmp/g1"), 1), (Path("/tmp/g2"), 3)],
        [(Path("/tmp/g3"), 1), (Path("/tmp/g4"), 3)],
    ]
    seen_matchups: list[list[tuple[int, int]]] = []

    def fake_run_games(tournament_arg, matchups, *, skip_compile=False):
        _ = tournament_arg, skip_compile
        seen_matchups.append(matchups)
        return results_by_call.pop(0)

    monkeypatch.setattr(tournament_game, "_run_games", fake_run_games)
    monkeypatch.setattr(tournament_game, "upload_and_export", lambda *_args, **_kwargs: None)

    tournament_game._run_match_batch(
        tournament,
        tournament_path,
        batch,
        skip_compile=True,
    )

    assert seen_matchups == [[(1, 4), (2, 3)], [(1, 4), (2, 3)]]
    assert batch[0][1]["winner_seed"] == 1
    assert batch[1][1]["winner_seed"] == 3
    assert [game["winner_seed"] for game in batch[0][1]["games"]] == [1, 1]
    assert [game["winner_seed"] for game in batch[1][1]["games"]] == [3, 3]


def test_main_parallel_uses_batch_runner(monkeypatch):
    """Parallel tournament mode should batch matches onto one orchestrator run."""
    tournament = {
        "season": 1,
        "size": 4,
        "best_of": 3,
        "elimination": "single",
        "draft": {"decklists": {}},
        "rounds": [],
        "entrants": [],
    }
    ready = [
        (
            {"round": 1, "name": "Semifinals"},
            {"match": 1, "seed_a": 1, "seed_b": 4, "winner_seed": None, "games": []},
        ),
        (
            {"round": 1, "name": "Semifinals"},
            {"match": 2, "seed_a": 2, "seed_b": 3, "winner_seed": None, "games": []},
        ),
    ]
    run_match_batch = MagicMock()

    monkeypatch.setattr(
        tournament_game,
        "load_tournament",
        lambda _allowed_phases=None: (tournament, Path("/tmp/tournament.json")),
    )
    monkeypatch.setattr(tournament_game, "compile_project", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(tournament_game, "refresh_observer_resources", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(tournament_game, "clean_stale_h2_locks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tournament_game, "find_ready_matches", lambda _tournament: ready)
    monkeypatch.setattr(tournament_game, "_run_match_batch", run_match_batch)
    monkeypatch.setattr(tournament_game, "resolve_annotation_failures", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tournament_game, "generate_all_website_data", lambda: None)
    monkeypatch.setattr(sys, "argv", ["tournament_game.py", "--games", "2"])

    assert tournament_game.main() == 0
    run_match_batch.assert_called_once()
    assert run_match_batch.call_args.kwargs["skip_compile"] is True
