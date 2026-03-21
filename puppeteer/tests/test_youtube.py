"""Tests for YouTube upload and related orchestrator functions."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from puppeteer.orchestrator import _save_youtube_url, _update_website_youtube_url, upload_and_export
from scripts.export_game import GameExportError, export_game
from scripts.upload_youtube import (
    YouTubeUploadError,
    _build_description,
    _build_title,
    upload_to_youtube,
)


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
        assert desc == "\n".join(
            [
                "AI models play Commander (Magic: The Gathering) via mage-bench.",
                "",
                "Alice playing Meren of Clan Nel Toth (openai/gpt-4)",
                "Bob playing Atraxa, Praetors' Voice (google/gemini-3-flash)",
                "",
                "Replay this game:",
                "https://mage-bench.com/games/game_20260210_120000",
                "",
                "https://mage-bench.com",
            ]
        )


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


def test_export_game_wraps_operational_errors():
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir) / "game_20260210_120000"
        website_games_dir = Path(tmpdir) / "website" / "public" / "games"

        with (
            patch(
                "scripts.export_game.build_export",
                side_effect=AssertionError("missing game_type"),
            ),
            pytest.raises(GameExportError, match="missing game_type"),
        ):
            export_game(game_dir, website_games_dir)


def test_upload_to_youtube_wraps_operational_errors():
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir) / "game_20260210_120000"
        game_dir.mkdir()
        (game_dir / "recording.mov").write_bytes(b"movie")

        with (
            patch(
                "scripts.upload_youtube._get_authenticated_service",
                side_effect=FileNotFoundError("missing client secrets"),
            ),
            pytest.raises(YouTubeUploadError, match="missing client secrets"),
        ):
            upload_to_youtube(game_dir)


def test_upload_and_export_returns_zero_without_api_key():
    """Without OPENROUTER_API_KEY, should export without annotation and return 0.0."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir) / "game_20260210_120000"
        game_dir.mkdir()
        project_root = Path(tmpdir)
        (project_root / "website" / "public" / "games").mkdir(parents=True)
        (game_dir / "game_meta.json").write_text(json.dumps(_make_meta()))
        (game_dir / "game_events.jsonl").write_text("")
        with (
            patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}, clear=False),
            patch("puppeteer.post_game_analysis._export_game") as mock_export,
            patch("puppeteer.post_game_analysis._upload_to_youtube"),
        ):
            # _export_game returns a path to the temp export file
            export_path = Path(tmpdir) / "export" / "game_20260210_120000.json"
            export_path.parent.mkdir(parents=True)
            export_path.write_text("{}")
            mock_export.return_value = export_path
            result = upload_and_export(game_dir, project_root)
    assert result == 0.0


def test_upload_and_export_skips_youtube_without_recording():
    """Without recording.mov, should skip YouTube upload but still export."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir) / "game_20260210_120000"
        game_dir.mkdir()
        project_root = Path(tmpdir)
        (project_root / "website" / "public" / "games").mkdir(parents=True)
        (game_dir / "game_meta.json").write_text(json.dumps(_make_meta()))
        (game_dir / "game_events.jsonl").write_text("")
        with (
            patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}, clear=False),
            patch("puppeteer.post_game_analysis._upload_to_youtube") as mock_yt,
            patch("puppeteer.post_game_analysis._export_game") as mock_export,
        ):
            export_path = Path(tmpdir) / "export" / "game_20260210_120000.json"
            export_path.parent.mkdir(parents=True)
            export_path.write_text("{}")
            mock_export.return_value = export_path
            upload_and_export(game_dir, project_root)
        mock_yt.assert_not_called()


def test_upload_and_export_continues_after_youtube_upload_error():
    """A YouTube upload failure should still export the game."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_id = "game_20260210_120000"
        game_dir = Path(tmpdir) / game_id
        game_dir.mkdir()
        (game_dir / "recording.mov").write_bytes(b"movie")
        project_root = Path(tmpdir)
        final_games_dir = project_root / "website" / "public" / "games"
        final_games_dir.mkdir(parents=True)
        (game_dir / "game_meta.json").write_text(json.dumps(_make_meta()))
        (game_dir / "game_events.jsonl").write_text("")
        with (
            patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}, clear=False),
            patch(
                "puppeteer.post_game_analysis._upload_to_youtube",
                side_effect=YouTubeUploadError("auth failed"),
            ),
            patch("puppeteer.post_game_analysis._export_game") as mock_export,
        ):
            export_path = Path(tmpdir) / "export" / f"{game_id}.json"
            export_path.parent.mkdir(parents=True)
            export_path.write_text("{}")
            mock_export.return_value = export_path

            result = upload_and_export(game_dir, project_root)

        assert result == 0.0
        assert (final_games_dir / f"{game_id}.json").exists()


def test_upload_and_export_returns_zero_on_export_error():
    """A website export failure should be logged and skipped."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir) / "game_20260210_120000"
        game_dir.mkdir()
        project_root = Path(tmpdir)
        (project_root / "website" / "public" / "games").mkdir(parents=True)
        (game_dir / "game_meta.json").write_text(json.dumps(_make_meta()))
        (game_dir / "game_events.jsonl").write_text("")
        with (
            patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}, clear=False),
            patch(
                "puppeteer.post_game_analysis._export_game",
                side_effect=GameExportError("bad export"),
            ),
        ):
            result = upload_and_export(game_dir, project_root)

        assert result == 0.0
