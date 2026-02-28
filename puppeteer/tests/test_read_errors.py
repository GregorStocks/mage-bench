"""Tests for _read_errors() in export_game.py."""

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _get_read_errors():
    """Import _read_errors from scripts/export_game.py."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from export_game import _read_errors

    return _read_errors


def test_read_errors_parses_pilot_and_mcp():
    _read_errors = _get_read_errors()
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        (game_dir / "Alice_errors.log").write_text(
            "[10:30:45] [pilot] Stalled: 20 turns\n[10:31:00] [mcp] choose_action failed: index out of range\n"
        )
        errors = _read_errors(game_dir)
        assert len(errors) == 2
        assert errors[0] == {
            "ts": "10:30:45",
            "player": "Alice",
            "source": "pilot",
            "message": "Stalled: 20 turns",
        }
        assert errors[1] == {
            "ts": "10:31:00",
            "player": "Alice",
            "source": "mcp",
            "message": "choose_action failed: index out of range",
        }


def test_read_errors_multiple_players():
    _read_errors = _get_read_errors()
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        (game_dir / "Alice_errors.log").write_text("[10:30:45] [pilot] Error A\n")
        (game_dir / "Bob_errors.log").write_text("[10:30:50] [mcp] Error B\n")
        errors = _read_errors(game_dir)
        assert len(errors) == 2
        players = {e["player"] for e in errors}
        assert players == {"Alice", "Bob"}


def test_read_errors_empty_dir():
    _read_errors = _get_read_errors()
    with tempfile.TemporaryDirectory() as tmpdir:
        errors = _read_errors(Path(tmpdir))
        assert errors == []


def test_read_errors_blank_lines_skipped():
    _read_errors = _get_read_errors()
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        (game_dir / "Alice_errors.log").write_text("\n[10:30:45] [pilot] Error\n\n")
        errors = _read_errors(game_dir)
        assert len(errors) == 1


def test_read_errors_malformed_line():
    _read_errors = _get_read_errors()
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        (game_dir / "Bob_errors.log").write_text("no timestamp here\n")
        errors = _read_errors(game_dir)
        assert len(errors) == 1
        assert errors[0]["source"] == "unknown"
        assert errors[0]["ts"] == ""
        assert errors[0]["message"] == "no timestamp here"


def test_read_errors_player_name_with_spaces():
    _read_errors = _get_read_errors()
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        (game_dir / "Gem3F Libby_errors.log").write_text("[10:30:45] [mcp] Loop detected\n")
        errors = _read_errors(game_dir)
        assert len(errors) == 1
        assert errors[0]["player"] == "Gem3F Libby"
