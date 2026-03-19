"""Tests for scripts/checks/lint_issues.py."""

from pathlib import Path

from scripts.checks.lint_issues import lint_issues
from scripts.json5_utils import dumps_json5


def _make_valid_issue() -> dict:
    return {
        "title": "Test issue",
        "description": "A test issue",
        "status": "open",
        "priority": 2,
        "type": "bug",
        "labels": ["test"],
        "created_at": "2026-03-01T12:00:00.000000-08:00",
        "updated_at": "2026-03-01T12:00:00.000000-08:00",
    }


def _write_issue(issues_dir: Path, name: str, data: dict) -> None:
    (issues_dir / f"{name}.json5").write_text(dumps_json5(data))


def test_passes_on_valid_issue(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    _write_issue(issues_dir, "p2-good-issue", _make_valid_issue())
    assert lint_issues(tmp_path) == []


def test_passes_with_optional_fields(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue = _make_valid_issue()
    issue["blocked"] = True
    _write_issue(issues_dir, "blocked-good-issue", issue)
    assert lint_issues(tmp_path) == []


def test_passes_with_string_blocked(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue = _make_valid_issue()
    issue["blocked"] = "Waiting for upstream dependency fix."
    _write_issue(issues_dir, "blocked-good-issue", issue)
    assert lint_issues(tmp_path) == []


def test_passes_on_json5_syntax(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    (issues_dir / "p2-json5-issue.json5").write_text(
        """{
  title: "Test issue",
  description: "A test issue",
  status: "open",
  priority: 2,
  type: "bug",
  labels: ["test"],
  created_at: "2026-03-01T12:00:00.000000-08:00",
  updated_at: "2026-03-01T12:00:00.000000-08:00",
}
"""
    )
    assert lint_issues(tmp_path) == []


def test_catches_missing_field(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue = _make_valid_issue()
    del issue["title"]
    _write_issue(issues_dir, "p2-bad-issue", issue)
    errors = lint_issues(tmp_path)
    assert len(errors) == 1
    assert "missing fields" in errors[0]
    assert "title" in errors[0]


def test_catches_unknown_field(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue = _make_valid_issue()
    issue["autograbbable"] = False
    _write_issue(issues_dir, "p2-bad-issue", issue)
    errors = lint_issues(tmp_path)
    assert len(errors) == 1
    assert "unknown fields" in errors[0]
    assert "autograbbable" in errors[0]


def test_catches_id_field(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue = _make_valid_issue()
    issue["id"] = "should-not-exist"
    _write_issue(issues_dir, "p2-bad-issue", issue)
    errors = lint_issues(tmp_path)
    assert any("has 'id' field" in e for e in errors)


def test_catches_priority_prefix_mismatch(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    _write_issue(issues_dir, "p3-bad-issue", _make_valid_issue())
    errors = lint_issues(tmp_path)
    assert any("filename prefix must be 'p2-'" in e for e in errors)


def test_catches_blocked_prefix_mismatch(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue = _make_valid_issue()
    issue["blocked"] = True
    _write_issue(issues_dir, "p2-bad-issue", issue)
    errors = lint_issues(tmp_path)
    assert any("filename prefix must be 'blocked-'" in e for e in errors)


def test_catches_missing_required_prefix(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    _write_issue(issues_dir, "bad-issue", _make_valid_issue())
    errors = lint_issues(tmp_path)
    assert any("filename must start with p1-/p2-/p3-/p4-/blocked-" in e for e in errors)


def test_catches_legacy_json_extension(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    (issues_dir / "p2-legacy.json").write_text("{}")
    errors = lint_issues(tmp_path)
    assert errors == ["p2-legacy.json: legacy issue file extension; rename to .json5"]


def test_real_issues_directory() -> None:
    """The actual issues/ directory must pass the lint check."""
    project_root = Path(__file__).resolve().parent.parent.parent
    errors = lint_issues(project_root)
    assert errors == [], f"Issue lint errors: {errors}"
