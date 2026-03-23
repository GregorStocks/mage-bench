"""Tests for magebench CLI source linting."""

from pathlib import Path

from magebench.cli.checks.lint_scripts_are_python import lint_scripts


def _make_scripts_dir(tmp_path: Path) -> Path:
    """Create a minimal src/magebench/cli tree that passes the lint check."""
    scripts = tmp_path / "src" / "magebench" / "cli"
    scripts.mkdir(parents=True)
    (scripts / "example.py").write_text("#!/usr/bin/env python3\n")
    return tmp_path


def test_passes_on_all_python(tmp_path: Path) -> None:
    root = _make_scripts_dir(tmp_path)
    assert lint_scripts(root) == []


def test_catches_shell_script(tmp_path: Path) -> None:
    root = _make_scripts_dir(tmp_path)
    (root / "src" / "magebench" / "cli" / "bad.sh").write_text("#!/bin/bash\n")
    errors = lint_scripts(root)
    assert len(errors) == 1
    assert "bad.sh" in errors[0]
    assert "not a Python script" in errors[0]


def test_ignores_json_data_files(tmp_path: Path) -> None:
    root = _make_scripts_dir(tmp_path)
    (root / "src" / "magebench" / "cli" / "data.json").write_text("{}\n")
    assert lint_scripts(root) == []


def test_ignores_gitkeep(tmp_path: Path) -> None:
    root = _make_scripts_dir(tmp_path)
    subdir = root / "src" / "magebench" / "cli" / "subdir"
    subdir.mkdir()
    (subdir / ".gitkeep").write_text("")
    assert lint_scripts(root) == []


def test_catches_in_subdirectory(tmp_path: Path) -> None:
    root = _make_scripts_dir(tmp_path)
    subdir = root / "src" / "magebench" / "cli" / "subdir"
    subdir.mkdir()
    (subdir / "nope.rb").write_text("#!/usr/bin/env ruby\n")
    errors = lint_scripts(root)
    assert len(errors) == 1
    assert "nope.rb" in errors[0]


def test_real_scripts_directory() -> None:
    """The actual CLI source directory must pass the lint check."""
    project_root = Path(__file__).resolve().parent.parent
    errors = lint_scripts(project_root)
    assert errors == [], f"CLI source lint errors: {errors}"
