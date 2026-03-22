"""Tests for blunder_audit.py path parsing."""

import pytest

from magebench.game.game_export_types import (
    Permanent,
    Snapshot,
    SnapshotPlayer,
    StackItem,
)
from scripts.analysis.blunder_audit import format_play_context, parse_viewer_url

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


class TestFormatPlayContext:
    def test_formats_dataclass_leaf_names_without_repr_noise(self) -> None:
        snapshots = [
            Snapshot(
                seq=1,
                turn=1,
                phase="MAIN",
                step="MAIN",
                active_player="Alice",
                priority_player="Alice",
                players=[
                    SnapshotPlayer(
                        name="Alice",
                        life=20,
                        library_size=53,
                        battlefield=[],
                        graveyard=[],
                        hand=[Permanent(name="Island")],
                    )
                ],
                stack=[StackItem(name="Lightning Bolt")],
            )
        ]
        decision = {
            "decision_index": 0,
            "snapshot_index": 0,
            "player": "Alice",
            "turn": 1,
            "phase": "MAIN",
            "message": "Choose action",
        }

        text = format_play_context(VALID_GAME_ID, decision, snapshots, None)

        assert "Stack: Lightning Bolt" in text
        assert "Hand: Island" in text
        assert "StackItem(" not in text
        assert "Permanent(" not in text
