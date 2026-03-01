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


def test_read_errors_parses_code_bugs():
    """Only infrastructure errors (code bugs) are returned."""
    _read_errors = _get_read_errors()
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        (game_dir / "Alice_errors.log").write_text(
            "[10:30:45] [mcp] Zombie game detected: no actionable callback for 15000ms\n"
            "[10:31:00] [mcp] Server short ID collision: p3 was mapped to abc but server now says def\n"
        )
        errors = _read_errors(game_dir)
        assert len(errors) == 2
        assert errors[0] == {
            "ts": "10:30:45",
            "player": "Alice",
            "source": "mcp",
            "message": "Zombie game detected: no actionable callback for 15000ms",
        }
        assert errors[1] == {
            "ts": "10:31:00",
            "player": "Alice",
            "source": "mcp",
            "message": "Server short ID collision: p3 was mapped to abc but server now says def",
        }


def test_read_errors_filters_llm_errors():
    """LLM mistakes (bad tool calls, loops, timeouts) are filtered out."""
    _read_errors = _get_read_errors()
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        (game_dir / "Alice_errors.log").write_text(
            "[10:30:45] [pilot] Stalled: 20 turns without progress\n"
            "[10:31:00] [mcp] choose_action failed: index out of range\n"
            "[10:31:05] [mcp] Loop detected (27 interactions this turn)\n"
            "[10:31:10] [mcp] Zombie game detected: no actionable callback for 15000ms\n"
            "[10:31:15] [pilot] LLM request timed out after 120s [3]\n"
            "[10:31:20] [pilot] Action failed: Object p3 not found\n"
            "[10:31:25] [mcp] MCP request failed (choose_action): bad json\n"
        )
        errors = _read_errors(game_dir)
        # Only the zombie game error should survive
        assert len(errors) == 1
        assert errors[0]["message"] == "Zombie game detected: no actionable callback for 15000ms"


def test_read_errors_multiple_players():
    _read_errors = _get_read_errors()
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        (game_dir / "Alice_errors.log").write_text(
            "[10:30:45] [mcp] Zombie game detected: no actionable callback for 15000ms\n"
        )
        (game_dir / "Bob_errors.log").write_text(
            "[10:30:50] [mcp] Error handling callback GAME_SELECT: NullPointerException\n"
        )
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
        (game_dir / "Alice_errors.log").write_text("\n[10:30:45] [mcp] Error handling callback GAME_SELECT: NPE\n\n")
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
        (game_dir / "Gem3F Libby_errors.log").write_text(
            "[10:30:45] [mcp] Zombie game detected: no actionable callback for 10000ms\n"
        )
        errors = _read_errors(game_dir)
        assert len(errors) == 1
        assert errors[0]["player"] == "Gem3F Libby"


def test_read_errors_iso_timestamp():
    """Java bridge writes ISO 8601 timestamps — should parse to HH:MM:SS."""
    _read_errors = _get_read_errors()
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        line = "[2026-02-28T14:33:38.795711643-08:00] [mcp] Zombie game detected: no actionable callback for 20000ms\n"
        (game_dir / "Alice_errors.log").write_text(line)
        errors = _read_errors(game_dir)
        assert len(errors) == 1
        assert errors[0] == {
            "ts": "14:33:38",
            "player": "Alice",
            "source": "mcp",
            "message": "Zombie game detected: no actionable callback for 20000ms",
        }


def test_read_errors_mixed_formats():
    """Python pilot (HH:MM:SS) and Java bridge (ISO) in same file."""
    _read_errors = _get_read_errors()
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        (game_dir / "Bot_errors.log").write_text(
            "[13:55:11] [mcp] Error handling callback GAME_SELECT: NPE\n"
            "[2026-02-28T14:33:38.123456789-08:00] [mcp]"
            " Server short ID collision: p3 was mapped to abc but server now says def\n"
        )
        errors = _read_errors(game_dir)
        assert len(errors) == 2
        assert errors[0]["ts"] == "13:55:11"
        assert errors[0]["source"] == "mcp"
        assert errors[1]["ts"] == "14:33:38"
        assert errors[1]["source"] == "mcp"
        assert errors[1]["message"].startswith("Server short ID collision:")
