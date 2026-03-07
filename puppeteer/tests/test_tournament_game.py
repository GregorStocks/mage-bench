"""Tests for tournament game runner.

Unit tests for bracket generation, round naming, next-match finding,
result recording, and deck file writing.
"""

from pathlib import Path

import pytest

from scripts.tournament_game import (
    find_next_match,
    generate_bracket,
    round_name,
    write_tournament_deck,
)

# -- Bracket generation --


class TestGenerateBracket:
    def test_size_2(self):
        assert generate_bracket(2) == [(1, 2)]

    def test_size_4(self):
        bracket = generate_bracket(4)
        assert bracket == [(1, 4), (2, 3)]
        # Seeds 1 and 2 are on opposite sides
        assert bracket[0][0] == 1
        assert bracket[1][0] == 2

    def test_size_8(self):
        bracket = generate_bracket(8)
        assert bracket == [(1, 8), (4, 5), (2, 7), (3, 6)]
        # Top half: 1v8, 4v5; bottom half: 2v7, 3v6
        # 1 and 2 can only meet in the final
        top_seeds = {s for pair in bracket[:2] for s in pair}
        bottom_seeds = {s for pair in bracket[2:] for s in pair}
        assert 1 in top_seeds and 2 in bottom_seeds

    def test_size_16(self):
        bracket = generate_bracket(16)
        assert len(bracket) == 8
        # All seeds 1-16 appear exactly once
        all_seeds = [s for pair in bracket for s in pair]
        assert sorted(all_seeds) == list(range(1, 17))
        # Each match pairs a high seed with a low seed (sum = 17)
        for a, b in bracket:
            assert a + b == 17

    def test_non_power_of_2_fails(self):
        with pytest.raises(AssertionError, match="power of 2"):
            generate_bracket(6)

    def test_size_1_fails(self):
        with pytest.raises(AssertionError, match="power of 2"):
            generate_bracket(1)


# -- Round naming --


class TestRoundName:
    def test_finals(self):
        assert round_name(1) == "Finals"

    def test_semifinals(self):
        assert round_name(2) == "Semifinals"

    def test_quarterfinals(self):
        assert round_name(4) == "Quarterfinals"

    def test_round_of_16(self):
        assert round_name(8) == "Round of 16"


# -- Finding next match --


def _make_tournament(size: int, rounds: list | None = None) -> dict:
    """Create a minimal tournament dict for testing."""
    return {
        "size": size,
        "entrants": [{"seed": i + 1} for i in range(size)],
        "rounds": rounds or [],
    }


class TestFindNextMatch:
    def test_empty_rounds_generates_round_1(self):
        t = _make_tournament(8)
        result = find_next_match(t)
        assert result is not None
        round_dict, match = result
        assert round_dict["round"] == 1
        assert round_dict["name"] == "Quarterfinals"
        assert len(round_dict["matches"]) == 4
        assert match["seed_a"] == 1
        assert match["seed_b"] == 8
        assert match["winner_seed"] is None
        # Round was added to tournament
        assert len(t["rounds"]) == 1

    def test_partially_complete_round(self):
        t = _make_tournament(4)
        # Generate round 1 first
        find_next_match(t)
        # Complete first match
        t["rounds"][0]["matches"][0]["winner_seed"] = 1
        # Should return second match
        result = find_next_match(t)
        assert result is not None
        _, match = result
        assert match["match"] == 2
        assert match["seed_a"] == 2
        assert match["seed_b"] == 3

    def test_complete_round_generates_next(self):
        t = _make_tournament(4)
        find_next_match(t)
        # Complete both round 1 matches
        t["rounds"][0]["matches"][0]["winner_seed"] = 1  # 1 beats 4
        t["rounds"][0]["matches"][1]["winner_seed"] = 2  # 2 beats 3
        # Should generate round 2 (finals)
        result = find_next_match(t)
        assert result is not None
        round_dict, match = result
        assert round_dict["round"] == 2
        assert round_dict["name"] == "Finals"
        assert match["seed_a"] == 1
        assert match["seed_b"] == 2
        assert len(t["rounds"]) == 2

    def test_tournament_complete(self):
        t = _make_tournament(2)
        find_next_match(t)
        # Complete the only match
        t["rounds"][0]["matches"][0]["winner_seed"] = 1
        result = find_next_match(t)
        assert result is None

    def test_8_player_full_bracket(self):
        t = _make_tournament(8)
        # Play through all 7 matches
        matches_played = 0
        while True:
            result = find_next_match(t)
            if result is None:
                break
            _, match = result
            # Higher seed (lower number) always wins
            winner = min(match["seed_a"], match["seed_b"])
            match["winner_seed"] = winner
            match["game_id"] = f"game_{matches_played}"
            matches_played += 1
        assert matches_played == 7
        assert len(t["rounds"]) == 3  # QF, SF, Finals
        # Seed 1 wins the tournament
        final = t["rounds"][-1]["matches"][0]
        assert final["winner_seed"] == 1


# -- Deck file writing --


class TestWriteTournamentDeck:
    def test_writes_dck_file(self, tmp_path: Path):
        cards = ["1 [JMP:1] Serra Angel", "7 [JMP:2] Plains"]
        half_decks = ["Angels", "Cats"]
        rel_path = write_tournament_deck(tmp_path, 3, cards, half_decks)
        full_path = tmp_path / rel_path
        assert full_path.exists()
        content = full_path.read_text()
        assert "NAME:Angels + Cats" in content
        assert "1 [JMP:1] Serra Angel" in content
        assert "7 [JMP:2] Plains" in content

    def test_filename_contains_seed(self, tmp_path: Path):
        rel_path = write_tournament_deck(tmp_path, 5, ["1 [JMP:1] Card"], ["Pack"])
        assert "seed-5" in str(rel_path)

    def test_path_is_relative(self, tmp_path: Path):
        rel_path = write_tournament_deck(tmp_path, 1, ["1 [JMP:1] Card"], ["Pack"])
        assert not rel_path.is_absolute()
