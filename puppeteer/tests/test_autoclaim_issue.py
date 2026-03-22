"""Tests for scripts/autoclaim_issue.py."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from magebench.common.json5_writer import dumps_json5
from magebench.common.local_claims import ClaimRecord

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"


def _import_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


autoclaim_issue = _import_script("autoclaim_issue")


def _claim_record(key: str) -> ClaimRecord:
    return ClaimRecord(
        namespace="issues",
        key=key,
        claim_path=Path(f"/tmp/{key}.json"),
        worktree_path=Path("/tmp/wt"),
        worktree_name="wt",
        branch="feature",
        payload={"key": key},
    )


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


def test_claim_specific_uses_local_claim_backend(tmp_path: Path, capsys) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    (issues_dir / "p1-first.json5").write_text(dumps_json5({"title": "First", "priority": 1}))
    autoclaim_issue.ISSUES_DIR = issues_dir

    with (
        patch.object(
            autoclaim_issue,
            "claim_exact_keys",
            return_value=[_claim_record("first")],
        ) as mock_claim,
        patch.object(autoclaim_issue, "current_owner_claims", return_value=[]),
    ):
        autoclaim_issue.claim_specific("p1-first")

    mock_claim.assert_called_once()
    assert capsys.readouterr().out == "Claimed: p1-first\n"


def test_main_auto_claims_first_available(tmp_path: Path, capsys) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    (issues_dir / "p1-first.json5").write_text(dumps_json5({"title": "First", "priority": 1}))
    autoclaim_issue.ISSUES_DIR = issues_dir

    with (
        patch.object(sys, "argv", ["autoclaim_issue.py"]),
        patch.object(autoclaim_issue, "merge_master"),
        patch.object(
            autoclaim_issue,
            "current_worktree_context",
            return_value=MagicMock(branch="feature"),
        ),
        patch.object(autoclaim_issue, "current_owner_claims", return_value=[]),
        patch.object(
            autoclaim_issue,
            "claim_first_available_keys",
            return_value=[_claim_record("first")],
        ) as mock_claim,
    ):
        autoclaim_issue.main()

    mock_claim.assert_called_once()
    assert capsys.readouterr().out == "Claimed: p1-first\n"


def test_main_exits_1_when_no_claimable_issue(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    autoclaim_issue.ISSUES_DIR = issues_dir

    with (
        patch.object(sys, "argv", ["autoclaim_issue.py"]),
        patch.object(autoclaim_issue, "merge_master"),
        patch.object(
            autoclaim_issue,
            "current_worktree_context",
            return_value=MagicMock(branch="feature"),
        ),
        patch.object(autoclaim_issue, "current_owner_claims", return_value=[]),
        patch.object(autoclaim_issue, "claim_first_available_keys", return_value=[]),
        pytest.raises(SystemExit, match="1"),
    ):
        autoclaim_issue.main()


def test_main_exits_2_when_worktree_already_claims_issue(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    (issues_dir / "p1-first.json5").write_text(dumps_json5({"title": "First", "priority": 1}))
    autoclaim_issue.ISSUES_DIR = issues_dir

    with (
        patch.object(sys, "argv", ["autoclaim_issue.py"]),
        patch.object(autoclaim_issue, "merge_master"),
        patch.object(
            autoclaim_issue,
            "current_worktree_context",
            return_value=MagicMock(branch="feature"),
        ),
        patch.object(
            autoclaim_issue,
            "current_owner_claims",
            return_value=[_claim_record("first")],
        ),
        pytest.raises(SystemExit, match="2"),
    ):
        autoclaim_issue.main()


def test_main_exits_2_on_master_branch(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    autoclaim_issue.ISSUES_DIR = issues_dir

    with (
        patch.object(sys, "argv", ["autoclaim_issue.py"]),
        patch.object(autoclaim_issue, "merge_master"),
        patch.object(
            autoclaim_issue,
            "current_worktree_context",
            return_value=MagicMock(branch="master"),
        ),
        pytest.raises(SystemExit, match="2"),
    ):
        autoclaim_issue.main()


def test_claim_specific_exits_2_when_worktree_already_claims_other_issue(
    tmp_path: Path,
) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    (issues_dir / "p1-first.json5").write_text(dumps_json5({"title": "First", "priority": 1}))
    (issues_dir / "p2-second.json5").write_text(dumps_json5({"title": "Second", "priority": 2}))
    autoclaim_issue.ISSUES_DIR = issues_dir

    with (
        patch.object(
            autoclaim_issue,
            "current_owner_claims",
            return_value=[_claim_record("first")],
        ),
        pytest.raises(SystemExit, match="2"),
    ):
        autoclaim_issue.claim_specific("p2-second")
