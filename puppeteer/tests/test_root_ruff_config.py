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
