"""Tests for scripts/migrate_old_exports.py."""

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from migrate_old_exports import migrate_export, migrate_file


def _make_v1_export() -> dict:
    """Create a minimal v1 (spectator-based) export for testing."""
    return {
        "id": "game_20260210_test",
        "timestamp": "2026-02-10T07:43:00-08:00",
        "totalTurns": 3,
        "winner": "Alice",
        "players": [
            {"name": "Alice", "type": "llm"},
            {"name": "Bob", "type": "llm"},
        ],
        "cardImages": {},
        "snapshots": [
            {
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "step": "PRECOMBAT_MAIN",
                "active_player": "Alice",
                "priority_player": "Alice",
                "seq": 10,
                "ts": "2026-02-10T07:44:00-08:00",
                "players": [
                    {
                        "name": "Alice",
                        "life": 20,
                        "library_count": 53,
                        "hand_count": 7,
                        "hand": [
                            {"name": "Forest", "mana_cost": ""},
                            {"name": "Island", "mana_cost": ""},
                        ],
                        "is_active": True,
                        "has_left": False,
                        "counters": [],
                        "commanders": [],
                        "battlefield": [],
                        "graveyard": [],
                        "exile": [],
                    },
                    {
                        "name": "Bob",
                        "life": 20,
                        "library_count": 53,
                        "hand_count": 7,
                        "hand": [],
                        "is_active": False,
                        "has_left": False,
                        "counters": [],
                        "commanders": [],
                        "battlefield": [],
                        "graveyard": [],
                        "exile": [],
                    },
                ],
                "stack": [],
            }
        ],
        "actions": [
            {"ts": "2026-02-10T07:44:01-08:00", "seq": 1, "message": "Alice draws"},
        ],
        "llmEvents": [],
        "gameOver": {"seq": 50, "message": "Player Alice is the winner"},
    }


class TestMigrateExport:
    def test_adds_version(self) -> None:
        data = _make_v1_export()
        assert "version" not in data
        changed = migrate_export(data)
        assert changed is True
        assert data["version"] == 2

    def test_adds_missing_top_level_fields(self) -> None:
        data = _make_v1_export()
        migrate_export(data)
        assert data["gameType"] == ""
        assert data["deckType"] == ""
        assert data["llmTrace"] == []

    def test_renames_library_count(self) -> None:
        data = _make_v1_export()
        migrate_export(data)
        player = data["snapshots"][0]["players"][0]
        assert "library_count" not in player
        assert player["library_size"] == 53

    def test_preserves_bonus_data(self) -> None:
        data = _make_v1_export()
        migrate_export(data)
        player = data["snapshots"][0]["players"][0]
        # hand_count, hand contents, is_active etc. should be preserved
        assert player["hand_count"] == 7
        assert len(player["hand"]) == 2
        assert player["is_active"] is True
        assert player["has_left"] is False
        assert player["commanders"] == []

    def test_skips_already_v2(self) -> None:
        data = _make_v1_export()
        data["version"] = 2
        changed = migrate_export(data)
        assert changed is False

    def test_idempotent(self) -> None:
        data = _make_v1_export()
        migrate_export(data)
        snapshot_before = json.dumps(data, sort_keys=True)
        changed = migrate_export(data)
        assert changed is False
        snapshot_after = json.dumps(data, sort_keys=True)
        assert snapshot_before == snapshot_after


class TestMigrateFile:
    def test_migrates_json_file(self, tmp_path: Path) -> None:
        data = _make_v1_export()
        path = tmp_path / "game_test.json"
        path.write_text(json.dumps(data))

        changed = migrate_file(path)
        assert changed is True

        result = json.loads(path.read_text())
        assert result["version"] == 2
        assert result["snapshots"][0]["players"][0].get("library_size") == 53
        assert "library_count" not in result["snapshots"][0]["players"][0]

    def test_migrates_gzip_file(self, tmp_path: Path) -> None:
        data = _make_v1_export()
        path = tmp_path / "game_test.json.gz"
        path.write_bytes(gzip.compress(json.dumps(data).encode()))

        changed = migrate_file(path)
        assert changed is True

        # File stays as .json.gz (preserves compression format)
        assert path.exists()
        result = json.loads(gzip.decompress(path.read_bytes()))
        assert result["version"] == 2
        assert result["snapshots"][0]["players"][0].get("library_size") == 53

    def test_dry_run_does_not_modify(self, tmp_path: Path) -> None:
        data = _make_v1_export()
        path = tmp_path / "game_test.json"
        path.write_text(json.dumps(data))
        original = path.read_text()

        changed = migrate_file(path, dry_run=True)
        assert changed is True
        assert path.read_text() == original

    def test_skips_v2_file(self, tmp_path: Path) -> None:
        data = _make_v1_export()
        data["version"] = 2
        path = tmp_path / "game_test.json"
        path.write_text(json.dumps(data))

        changed = migrate_file(path)
        assert changed is False
