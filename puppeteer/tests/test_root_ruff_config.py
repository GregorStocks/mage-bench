"""Tests for repo-root Ruff configuration."""

import subprocess
from pathlib import Path

import pytest


def test_root_ruff_config_catches_import_outside_toplevel() -> None:
    project_root = Path(__file__).resolve().parent.parent.parent
    source = "def f():\n    import json\n    return json\n"

    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "puppeteer",
            "ruff",
            "check",
            "--config",
            "ruff-lint.toml",
            "--stdin-filename",
            "scripts/_lint_import_outside_toplevel_repro.py",
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


@pytest.mark.parametrize(
    ("stdin_filename", "source"),
    [
        (
            "scripts/_lint_boolean_trap_repro.py",
            "def f(flag: bool = False):\n    return flag\n",
        ),
        (
            "puppeteer/tests/_lint_boolean_trap_repro.py",
            "def f(flag: bool = False):\n    return flag\n",
        ),
    ],
)
def test_root_ruff_config_catches_boolean_traps(
    stdin_filename: str,
    source: str,
) -> None:
    project_root = Path(__file__).resolve().parent.parent.parent

    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "puppeteer",
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
            "puppeteer/tests/_lint_unused_argument_repro.py",
            "class T:\n    def f(self, arg):\n        return 1\n\nx = lambda y: 1\n",
            ["ARG002", "ARG005"],
        ),
        (
            "puppeteer/src/puppeteer/orchestrator.py",
            "def f(arg):\n    return 1\n",
            ["ARG001"],
        ),
        (
            "schemas/game_export_types.py",
            "def f(arg):\n    return 1\n",
            ["ARG001"],
        ),
        (
            "scripts/analysis/blunder_analysis.py",
            "def f(arg):\n    return 1\n",
            ["ARG001"],
        ),
        (
            "scripts/analysis/blunder_audit.py",
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
    project_root = Path(__file__).resolve().parent.parent.parent

    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "puppeteer",
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
