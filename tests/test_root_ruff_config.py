"""Tests for repo-root Ruff configuration."""

import subprocess
import tomllib
from pathlib import Path

import pytest

_PLC0415_TARGET_PATTERNS = (
    "src/magebench/analysis/blunder/blunder_analysis.py",
    "src/magebench/analysis/blunder/blunder_audit.py",
    "src/magebench/analysis/blunder/blunder_audit_web.py",
    "src/magebench/analysis/blunder/blunder_eval.py",
    "src/magebench/analysis/blunder/blunder_promote.py",
    "src/magebench/analysis/blunder/blunder_seed.py",
    "src/magebench/analysis/blunder/extract_decisions.py",
    "src/magebench/common/youtube_upload.py",
    "src/magebench/cli/export_game.py",
    "src/magebench/cli/tournament_draft.py",
)

_PLC0415_TARGET_PATHS = (
    "src/magebench/analysis/blunder/blunder_analysis.py",
    "src/magebench/analysis/blunder/blunder_audit.py",
    "src/magebench/analysis/blunder/blunder_audit_web.py",
    "src/magebench/analysis/blunder/blunder_eval.py",
    "src/magebench/analysis/blunder/blunder_promote.py",
    "src/magebench/analysis/blunder/blunder_seed.py",
    "src/magebench/analysis/blunder/extract_decisions.py",
    "src/magebench/common/youtube_upload.py",
    "src/magebench/cli/export_game.py",
    "src/magebench/cli/tournament_draft.py",
)


def test_root_ruff_config_catches_import_outside_toplevel() -> None:
    project_root = Path(__file__).resolve().parent.parent
    source = "def f():\n    import json\n    return json\n"

    result = subprocess.run(
        [
            "uv",
            "run",
            "ruff",
            "check",
            "--config",
            "ruff-lint.toml",
            "--stdin-filename",
            "src/magebench/cli/_lint_import_outside_toplevel_repro.py",
            "-",
        ],
        input=source,
        capture_output=True,
        text=True,
        check=False,
        cwd=project_root,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "PLC0415" in output


def test_root_ruff_config_does_not_ignore_plc0415_for_analysis_and_export_targets() -> None:
    project_root = Path(__file__).resolve().parent.parent
    config = tomllib.loads((project_root / "ruff-lint.toml").read_text())
    per_file_ignores = config["lint"]["per-file-ignores"]

    for pattern in _PLC0415_TARGET_PATTERNS:
        ignores = per_file_ignores.get(pattern, [])
        assert "PLC0415" not in ignores, f"{pattern} still suppresses PLC0415"


def test_target_analysis_and_export_scripts_pass_plc0415() -> None:
    project_root = Path(__file__).resolve().parent.parent

    result = subprocess.run(
        [
            "uv",
            "run",
            "ruff",
            "check",
            "--config",
            "ruff-lint.toml",
            "--select",
            "PLC0415",
            *_PLC0415_TARGET_PATHS,
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=project_root,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output


@pytest.mark.parametrize(
    ("stdin_filename", "source"),
    [
        (
            "src/magebench/cli/_lint_boolean_trap_repro.py",
            "def f(flag: bool = False):\n    return flag\n",
        ),
        (
            "tests/_lint_boolean_trap_repro.py",
            "def f(flag: bool = False):\n    return flag\n",
        ),
    ],
)
def test_root_ruff_config_catches_boolean_traps(
    stdin_filename: str,
    source: str,
) -> None:
    project_root = Path(__file__).resolve().parent.parent

    result = subprocess.run(
        [
            "uv",
            "run",
            "ruff",
            "check",
            "--config",
            "ruff-lint.toml",
            "--stdin-filename",
            stdin_filename,
            "-",
        ],
        input=source,
        capture_output=True,
        text=True,
        check=False,
        cwd=project_root,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "FBT001" in output


@pytest.mark.parametrize(
    ("stdin_filename", "source", "expected_codes"),
    [
        (
            "tests/_lint_unused_argument_repro.py",
            "class T:\n    def f(self, arg):\n        return 1\n\nx = lambda y: 1\n",
            ["ARG002", "ARG005"],
        ),
        (
            "src/magebench/orchestration/orchestrator.py",
            "def f(arg):\n    return 1\n",
            ["ARG001"],
        ),
        (
            "src/magebench/game/game_export_types.py",
            "def f(arg):\n    return 1\n",
            ["ARG001"],
        ),
        (
            "src/magebench/analysis/blunder/blunder_analysis.py",
            "def f(arg):\n    return 1\n",
            ["ARG001"],
        ),
        (
            "src/magebench/analysis/blunder/blunder_audit.py",
            "def f(arg):\n    return 1\n",
            ["ARG001"],
        ),
    ],
)
def test_root_ruff_config_catches_unused_arguments(
    stdin_filename: str,
    source: str,
    expected_codes: list[str],
) -> None:
    project_root = Path(__file__).resolve().parent.parent

    result = subprocess.run(
        [
            "uv",
            "run",
            "ruff",
            "check",
            "--config",
            "ruff-lint.toml",
            "--select",
            "ARG",
            "--stdin-filename",
            stdin_filename,
            "-",
        ],
        input=source,
        capture_output=True,
        text=True,
        check=False,
        cwd=project_root,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 1
    for expected_code in expected_codes:
        assert expected_code in output
