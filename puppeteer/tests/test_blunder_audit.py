"""Tests for blunder_audit.py path parsing."""

import pytest

from scripts.analysis.blunder_audit import parse_viewer_url

VALID_GAME_ID = "game_20260214_005111_g1"


class TestParseViewerUrl:
    def test_accepts_games_path(self) -> None:
        game_id, snapshot = parse_viewer_url(f"/games/{VALID_GAME_ID}?s=77")
        assert game_id == VALID_GAME_ID
        assert snapshot == 77

    def test_accepts_bare_game_id(self) -> None:
        game_id, snapshot = parse_viewer_url(f"{VALID_GAME_ID}?s=12")
        assert game_id == VALID_GAME_ID
        assert snapshot == 12

    def test_rejects_nested_path(self) -> None:
        with pytest.raises(AssertionError, match="Invalid viewer path"):
            parse_viewer_url(f"/games/{VALID_GAME_ID}/extra?s=9")

    def test_rejects_invalid_game_id(self) -> None:
        with pytest.raises(AssertionError, match="Invalid game_id"):
            parse_viewer_url("/games/not_a_game?s=1")
