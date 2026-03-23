import json
from pathlib import Path

from magebench.cli.migrate_draft_histories import migrate_tournament
from magebench.game.draft_history import CURRENT_DRAFT_HISTORY_VERSION


def _write_tournament(path: Path, *, history_version: int) -> None:
    tournament = {
        "draft": {
            "history_version": history_version,
            "picks": [
                {
                    "pack": ["Alpha", "Beta"],
                    "choice": "Alpha",
                    "reasoning": "Alpha is stronger.",
                    "thinking": "Compare removal and curve.",
                }
            ],
        }
    }
    path.write_text(json.dumps(tournament) + "\n")


def test_migrate_tournament_dry_run_leaves_file_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "season-1.json"
    _write_tournament(path, history_version=1)
    original = path.read_text()

    migrated = migrate_tournament(
        path,
        CURRENT_DRAFT_HISTORY_VERSION,
        dry_run=True,
        force=False,
    )

    assert migrated is True
    assert path.read_text() == original


def test_migrate_tournament_updates_draft_history_in_place(tmp_path: Path) -> None:
    path = tmp_path / "season-1.json"
    _write_tournament(path, history_version=1)

    migrated = migrate_tournament(
        path,
        CURRENT_DRAFT_HISTORY_VERSION,
        dry_run=False,
        force=False,
    )

    migrated_data = json.loads(path.read_text())

    assert migrated is True
    assert migrated_data["draft"]["history_version"] == CURRENT_DRAFT_HISTORY_VERSION
    assert migrated_data["draft"]["picks"][0]["attempts"] == [
        {
            "attempt": 1,
            "response": "Alpha is stronger.",
            "accepted": True,
            "thinking": "Compare removal and curve.",
        }
    ]
    assert "thinking" not in migrated_data["draft"]["picks"][0]
