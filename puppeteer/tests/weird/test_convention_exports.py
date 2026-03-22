"""Convention tests for exported game files."""

from collections.abc import Mapping
from pathlib import Path

import pytest

from schemas.game_export_migrations import CURRENT_GAME_EXPORT_VERSION
from tests.weird import repo_convention_helpers
from tests.weird.repo_convention_helpers import (
    PUPPETEER_DIR,
    RETIRED_MODELS,
    changed_files_since_master,
    glob_game_files,
    load_json,
)


class TestAllExportsValid:
    @pytest.mark.parametrize(
        "game_file",
        glob_game_files(),
        ids=lambda p: p.name,
    )
    def test_game_conforms_to_schema(
        self, game_file: Path, all_games_data: Mapping[Path, dict], game_export_validator
    ) -> None:
        data = all_games_data[game_file]
        version = data["version"]
        assert version in game_export_validator, f"No schema for version {version}"
        validator = game_export_validator[version]
        errors = sorted(validator.iter_errors(data), key=lambda error: list(error.absolute_path))
        assert not errors, f"{errors[0].message} (at {'/'.join(str(part) for part in errors[0].absolute_path)})"

    @pytest.mark.parametrize(
        "game_file",
        glob_game_files(),
        ids=lambda p: p.name,
    )
    def test_game_uses_current_wire_version(self, game_file: Path, all_games_data: Mapping[Path, dict]) -> None:
        assert all_games_data[game_file]["version"] == CURRENT_GAME_EXPORT_VERSION


class TestExportedGameModelsKnown:
    def test_game_models_exist(self, all_games_data: Mapping[Path, dict]) -> None:
        """Every player.model in exported games must be in models.json or the retired allowlist."""
        models_data = load_json(PUPPETEER_DIR / "models.json")
        model_ids = {model["id"] for model in models_data["models"]}
        allowed = model_ids | RETIRED_MODELS

        changed = changed_files_since_master()
        if changed is not None and "puppeteer/models.json" not in changed:
            game_files = glob_game_files()
        else:
            game_files = list(all_games_data.keys())

        unknown: list[str] = []
        for game_file in game_files:
            data = all_games_data[game_file]
            for player in data.get("players", []):
                model = player.get("model")
                if model and model not in allowed:
                    unknown.append(f"{game_file.name}: {model!r}")

        assert not unknown, (
            "Exported games reference unknown models (add to RETIRED_MODELS if intentional):\n  " + "\n  ".join(unknown)
        )


class TestChangedGameFilenames:
    def test_moved_schema_path_forces_full_export_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            repo_convention_helpers,
            "changed_files_since_master",
            lambda: {"src/magebench/game/game-export-v9.schema.json"},
        )

        assert repo_convention_helpers.changed_game_filenames() is None
