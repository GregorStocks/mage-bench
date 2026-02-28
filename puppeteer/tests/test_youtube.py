"""Tests for YouTube upload and related orchestrator functions."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from upload_youtube import _build_description, _build_title

from puppeteer.orchestrator import _save_youtube_url, _update_website_youtube_url


def _make_meta(players=None):
    """Build a minimal game_meta.json dict."""
    if players is None:
        players = [
            {
                "name": "Alice",
                "type": "pilot",
                "model": "openai/gpt-4",
                "decklist": ["SB: 1 [C15:49] Meren of Clan Nel Toth"],
            },
            {
                "name": "Bob",
                "type": "pilot",
                "model": "google/gemini-3-flash",
                "decklist": ["SB: 1 [C16:28] Atraxa, Praetors' Voice"],
            },
        ]
    return {
        "timestamp": "20260210_120000",
        "deck_type": "Variant Magic - Freeform Commander",
        "players": players,
    }


def test_build_title_basic():
    meta = _make_meta()
    title = _build_title(meta)
    assert "mage-bench" in title
    assert "Alice" in title
    assert "Meren of Clan Nel Toth" in title
    assert "Bob" in title
    assert "Atraxa, Praetors' Voice" in title


def test_build_title_truncates():
    """Title should be at most 100 chars."""
    players = [
        {"name": f"Player{i}", "decklist": [f"SB: 1 [C15:{i}] Very Long Commander Name Number {i}"], "type": "pilot"}
        for i in range(6)
    ]
    meta = _make_meta(players)
    title = _build_title(meta)
    assert len(title) <= 100


def test_build_title_no_commander():
    """Falls back to player name when no commander."""
    meta = _make_meta([{"name": "Alice", "type": "cpu", "decklist": []}])
    title = _build_title(meta)
    assert "Alice" in title


def test_build_title_non_commander_uses_deck_filename():
    """Non-commander formats should use deck filename, not sideboard card."""
    meta = {
        "timestamp": "20260210_120000",
        "deck_type": "Constructed - Legacy",
        "players": [
            {
                "name": "Alice",
                "type": "pilot",
                "model": "openai/gpt-4",
                "deck_path": "Mage.Client/release/sample-decks/Legacy/Izzet-Delver.dck",
                "decklist": ["SB: 1 [MH2:52] Subtlety"],
            },
            {
                "name": "Bob",
                "type": "pilot",
                "model": "google/gemini-3-flash",
                "deck_path": "Mage.Client/release/sample-decks/Legacy/Eldrazi-Stompy.dck",
                "decklist": ["SB: 1 [BFZ:15] Ulamog, the Ceaseless Hunger"],
            },
        ],
    }
    title = _build_title(meta)
    assert "Izzet Delver" in title
    assert "Eldrazi Stompy" in title
    # Should NOT contain sideboard card names
    assert "Subtlety" not in title
    assert "Ulamog" not in title


def test_build_description():
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir) / "game_20260210_120000"
        game_dir.mkdir()
        meta = _make_meta()
        desc = _build_description(meta, game_dir)
        assert "Alice" in desc
        assert "Meren of Clan Nel Toth" in desc
        assert "openai/gpt-4" in desc
        assert "https://mage-bench.com/games/game_20260210_120000" in desc
        assert "https://mage-bench.com" in desc


def test_save_youtube_url():
    """Should add youtube_url to game_meta.json."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        meta = {"timestamp": "20260210_120000", "players": []}
        (game_dir / "game_meta.json").write_text(json.dumps(meta))

        _save_youtube_url(game_dir, "https://youtu.be/abc123")

        updated = json.loads((game_dir / "game_meta.json").read_text())
        assert updated["youtube_url"] == "https://youtu.be/abc123"
        assert updated["timestamp"] == "20260210_120000"


def test_save_youtube_url_no_meta():
    """Should do nothing if game_meta.json doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        _save_youtube_url(game_dir, "https://youtu.be/abc123")
        assert not (game_dir / "game_meta.json").exists()


def test_update_website_youtube_url_patches_game_json():
    """Should patch youtubeUrl into the per-game website JSON."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        games_dir = project_root / "website" / "public" / "games"
        games_dir.mkdir(parents=True)

        game_id = "game_20260210_120000"
        game_dir = Path(tmpdir) / game_id

        game_data = {"id": game_id, "totalTurns": 10}
        (games_dir / f"{game_id}.json").write_text(json.dumps(game_data))

        index_data = [{"id": game_id, "totalTurns": 10}]
        (games_dir / "index.json").write_text(json.dumps(index_data))

        _update_website_youtube_url(game_dir, "https://youtu.be/xyz", project_root)

        updated_game = json.loads((games_dir / f"{game_id}.json").read_text())
        assert updated_game["youtubeUrl"] == "https://youtu.be/xyz"

        updated_index = json.loads((games_dir / "index.json").read_text())
        assert updated_index[0]["youtubeUrl"] == "https://youtu.be/xyz"


def test_update_website_youtube_url_no_files():
    """Should do nothing if website files don't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        game_dir = Path(tmpdir) / "game_20260210_120000"
        # Should not raise
        _update_website_youtube_url(game_dir, "https://youtu.be/xyz", project_root)


def test_maybe_upload_and_export_defaults_to_no(capsys):
    """Empty input should NOT trigger upload or export (default is N)."""
    from puppeteer.orchestrator import _maybe_upload_and_export

    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        project_root = Path(tmpdir)
        (game_dir / "recording.mov").write_bytes(b"fake")
        with patch("builtins.input", return_value=""):
            result = _maybe_upload_and_export(game_dir, project_root)
    output = capsys.readouterr().out
    assert "Exported" not in output
    assert "YouTube" not in output
    assert result is False


def test_maybe_upload_and_export_skips_without_recording():
    """Without recording.mov, should prompt for export only (not YouTube)."""
    from puppeteer.orchestrator import _maybe_upload_and_export

    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        project_root = Path(tmpdir)
        with patch("builtins.input", return_value="n") as mock_input:
            result = _maybe_upload_and_export(game_dir, project_root)
        mock_input.assert_called_once()
        assert result is False


def test_maybe_upload_and_export_reprompts_on_bad_input(caplog):
    """Unrecognized input should re-ask, not silently skip."""
    from puppeteer.orchestrator import _maybe_upload_and_export

    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        project_root = Path(tmpdir)
        (game_dir / "recording.mov").write_bytes(b"fake")
        with caplog.at_level("INFO", logger="puppeteer.orchestrator"), patch("builtins.input", side_effect=["yy", "n"]):
            _maybe_upload_and_export(game_dir, project_root)
    output = caplog.text
    assert "Unrecognized answer" in output


def test_maybe_upload_and_export_all_returns_true():
    """Answering 'all' should return True to auto-yes remaining games."""
    from puppeteer.orchestrator import _maybe_upload_and_export

    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        project_root = Path(tmpdir)
        with patch("builtins.input", return_value="all"), patch("puppeteer.orchestrator.sys") as mock_sys:
            mock_sys.path = []
            result = _maybe_upload_and_export(game_dir, project_root)
    assert result is True


def test_maybe_upload_and_export_auto_yes_skips_prompt():
    """auto_yes=True should skip the prompt entirely."""
    from puppeteer.orchestrator import _maybe_upload_and_export

    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        project_root = Path(tmpdir)
        with patch("builtins.input") as mock_input:
            result = _maybe_upload_and_export(game_dir, project_root, auto_yes=True)
        mock_input.assert_not_called()
        assert result is True
