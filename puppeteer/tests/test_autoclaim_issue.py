"""Tests for scripts/autoclaim-issue.py."""

import importlib.util
from pathlib import Path

from scripts.json5_utils import dumps_json5

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"


def _import_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


autoclaim_issue = _import_script("autoclaim-issue")


def test_load_issues_skips_blocked_and_sorts_by_priority(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    (issues_dir / "p3-third.json5").write_text(dumps_json5({"title": "Third", "priority": 3}))
    (issues_dir / "blocked-manual.json5").write_text(dumps_json5({"title": "Manual", "priority": 1, "blocked": True}))
    (issues_dir / "p1-first.json5").write_text(
        """{
  title: "First",
  priority: 1,
}
"""
    )

    autoclaim_issue.ISSUES_DIR = issues_dir

    assert autoclaim_issue.load_issues() == [
        ("p1-first", 1, "First"),
        ("p3-third", 3, "Third"),
    ]
