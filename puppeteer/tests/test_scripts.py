"""Tests for scripts/ Python rewrites."""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from scripts import scryfall

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
CLAIM_NS = 946688400000000000


def _import_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _claim_body(issue: str, claim_ts: int | None = None) -> str:
    body = f"<!-- claim: {issue} -->"
    if claim_ts is not None:
        body += f"\n<!-- claim-ts: {claim_ts} -->"
    return body


def _open_claim_pr(number: int, issue: str, created_at: str, claim_ts: int | None = None) -> dict[str, object]:
    return {
        "number": number,
        "body": _claim_body(issue, claim_ts),
        "createdAt": created_at,
    }


def _run_result(stdout: str, returncode: int = 0) -> MagicMock:
    result = MagicMock()
    result.stdout = stdout
    result.returncode = returncode
    return result


list_issues = _import_script("list-issues")
claim_issue = _import_script("claim-issue")
finalize_issue_pr = _import_script("finalize-issue-pr")
worktree_setup = _import_script("worktree-setup")
import_deck = _import_script("import-deck")
import_metagame = _import_script("import-metagame")
conclude_season = _import_script("conclude_season")
conclude_tournament = _import_script("conclude_tournament")
game_gz_bootstrap = _import_script("game-gz-bootstrap")


# ===========================================================================
# list-issues
# ===========================================================================


class TestListIssues:
    def test_sorted_by_priority(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        (issues_dir / "bug-a.json").write_text(json.dumps({"title": "Bug A", "priority": 3}))
        (issues_dir / "bug-b.json").write_text(json.dumps({"title": "Bug B", "priority": 1}))
        (issues_dir / "bug-c.json").write_text(json.dumps({"title": "Bug C", "priority": 2}))

        with patch.object(list_issues, "ISSUES_DIR", issues_dir):
            list_issues.main()

        out = capsys.readouterr().out
        lines = out.strip().splitlines()
        assert len(lines) == 3
        assert lines[0].startswith("bug-b:")  # priority 1
        assert lines[1].startswith("bug-c:")  # priority 2
        assert lines[2].startswith("bug-a:")  # priority 3

    def test_output_format(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        (issues_dir / "my-issue.json").write_text(json.dumps({"title": "My Title", "priority": 2}))

        with patch.object(list_issues, "ISSUES_DIR", issues_dir):
            list_issues.main()

        out = capsys.readouterr().out.strip()
        assert out == "my-issue: 2\tMy Title"


# ===========================================================================
# claim-issue
# ===========================================================================


class TestClaimIssue:
    def test_list_claimed(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "<!-- claim: bug-a -->\n<!-- claim: bug-b -->\nsome other body\n"

        with patch.object(claim_issue, "run", return_value=mock_result):
            result = claim_issue.list_claimed()

        assert result == ["bug-a", "bug-b"]

    def test_list_claimed_empty(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "no claims here\n"

        with patch.object(claim_issue, "run", return_value=mock_result):
            result = claim_issue.list_claimed()

        assert result == []

    def test_list_claimed_gh_failure(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch.object(claim_issue, "run", return_value=mock_result):
            result = claim_issue.list_claimed()

        assert result == []

    def test_missing_issue_exits_2(self, tmp_path: Path) -> None:
        with (
            patch.object(claim_issue, "ISSUES_DIR", tmp_path),
            patch.object(sys, "argv", ["claim-issue.py", "nonexistent"]),
            pytest.raises(SystemExit, match="2"),
        ):
            claim_issue.main()

    def test_no_args_exits_2(self) -> None:
        with patch.object(sys, "argv", ["claim-issue.py"]), pytest.raises(SystemExit, match="2"):
            claim_issue.main()

    def test_master_branch_exits_2(self, tmp_path: Path) -> None:
        issues_dir = tmp_path
        (issues_dir / "bug-a.json").write_text(json.dumps({"title": "Bug A", "priority": 1}))

        branch_result = MagicMock()
        branch_result.stdout = "master\n"

        with (
            patch.object(claim_issue, "ISSUES_DIR", issues_dir),
            patch.object(sys, "argv", ["claim-issue.py", "bug-a"]),
            patch.object(claim_issue, "run", return_value=branch_result),
            pytest.raises(SystemExit, match="2"),
        ):
            claim_issue.main()

    def test_conflicting_open_branch_pr_exits_2(self, tmp_path: Path) -> None:
        issues_dir = tmp_path
        (issues_dir / "bug-a.json").write_text(json.dumps({"title": "Bug A", "priority": 1}))

        branch_result = MagicMock()
        branch_result.stdout = "my-branch\n"

        branch_pr_result = MagicMock()
        branch_pr_result.returncode = 0
        branch_pr_result.stdout = json.dumps(
            [
                {
                    "number": 1059,
                    "body": _claim_body("bug-b"),
                    "url": "https://example.test/pr/1059",
                }
            ]
        )
        winner_result = _run_result(json.dumps([_open_claim_pr(1059, "bug-b", "2000-01-01T00:00:00Z")]))

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:2] == ["git", "branch"]:
                return branch_result
            if cmd[:3] == ["gh", "pr", "list"] and "--head" in cmd:
                return branch_pr_result
            if cmd[:3] == ["gh", "pr", "list"]:
                return winner_result
            raise AssertionError(f"unexpected run: {cmd}")

        with (
            patch.object(claim_issue, "ISSUES_DIR", issues_dir),
            patch.object(sys, "argv", ["claim-issue.py", "bug-a"]),
            patch.object(claim_issue, "run", side_effect=fake_run),
            patch("subprocess.run") as mock_subprocess,
            pytest.raises(SystemExit, match="2"),
        ):
            claim_issue.main()

        mock_subprocess.assert_not_called()

    def test_stale_branch_pr_for_other_issue_is_retargeted(self, tmp_path: Path) -> None:
        issues_dir = tmp_path
        (issues_dir / "bug-a.json").write_text(json.dumps({"title": "Bug A", "priority": 1}))
        (issues_dir / "bug-b.json").write_text(json.dumps({"title": "Bug B", "priority": 2}))

        branch_result = MagicMock()
        branch_result.stdout = "my-branch\n"

        stale_branch_pr = MagicMock()
        stale_branch_pr.returncode = 0
        stale_branch_pr.stdout = json.dumps(
            [
                {
                    "number": 1059,
                    "body": _claim_body("bug-a"),
                    "url": "https://example.test/pr/1059",
                }
            ]
        )

        retargeted_branch_pr = MagicMock()
        retargeted_branch_pr.returncode = 0
        retargeted_branch_pr.stdout = json.dumps(
            [
                {
                    "number": 1059,
                    "body": _claim_body("bug-b", CLAIM_NS),
                    "url": "https://example.test/pr/1059",
                }
            ]
        )

        branch_pr_results = iter([stale_branch_pr, retargeted_branch_pr])

        open_claim_results = iter(
            [
                _run_result(
                    json.dumps(
                        [
                            _open_claim_pr(42, "bug-a", "2000-01-01T00:00:00Z"),
                            _open_claim_pr(1059, "bug-a", "2000-01-01T01:00:00Z"),
                        ]
                    )
                ),
                _run_result(json.dumps([_open_claim_pr(1059, "bug-b", "2000-01-01T01:00:00Z", CLAIM_NS)])),
                _run_result(json.dumps([_open_claim_pr(1059, "bug-b", "2000-01-01T01:00:00Z", CLAIM_NS)])),
            ]
        )

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:2] == ["git", "branch"]:
                return branch_result
            if cmd[:3] == ["gh", "pr", "list"] and "--head" in cmd:
                return next(branch_pr_results)
            if cmd[:3] == ["gh", "pr", "list"]:
                return next(open_claim_results)
            raise AssertionError(f"unexpected run: {cmd}")

        with (
            patch.object(claim_issue, "ISSUES_DIR", issues_dir),
            patch.object(sys, "argv", ["claim-issue.py", "bug-b"]),
            patch.object(claim_issue, "run", side_effect=fake_run),
            patch.object(claim_issue.time, "sleep") as mock_sleep,
            patch.object(claim_issue.time, "time_ns", return_value=CLAIM_NS),
            patch("subprocess.run") as mock_subprocess,
        ):
            claim_issue.main()

        mock_sleep.assert_called_once_with(claim_issue.RACE_SETTLE_SECONDS)
        assert mock_subprocess.call_args_list == [
            call(
                [
                    "gh",
                    "pr",
                    "edit",
                    "1059",
                    "--title",
                    "Solve: Bug B",
                    "--body",
                    f"<!-- claim: bug-b -->\n<!-- claim-ts: {CLAIM_NS} -->",
                ],
                check=True,
            ),
            call(["git", "push", "-u", "origin", "my-branch"], check=True),
        ]

    def test_stale_branch_pr_does_not_hijack_existing_target_claim(self, tmp_path: Path) -> None:
        issues_dir = tmp_path
        (issues_dir / "bug-a.json").write_text(json.dumps({"title": "Bug A", "priority": 1}))
        (issues_dir / "bug-b.json").write_text(json.dumps({"title": "Bug B", "priority": 2}))

        branch_result = MagicMock()
        branch_result.stdout = "my-branch\n"

        stale_branch_pr = MagicMock()
        stale_branch_pr.returncode = 0
        stale_branch_pr.stdout = json.dumps(
            [
                {
                    "number": 1059,
                    "body": _claim_body("bug-a"),
                    "url": "https://example.test/pr/1059",
                }
            ]
        )

        retargeted_branch_pr = MagicMock()
        retargeted_branch_pr.returncode = 0
        retargeted_branch_pr.stdout = json.dumps(
            [
                {
                    "number": 1059,
                    "body": _claim_body("bug-b", CLAIM_NS),
                    "url": "https://example.test/pr/1059",
                }
            ]
        )

        branch_pr_results = iter([stale_branch_pr, retargeted_branch_pr])

        open_claim_results = iter(
            [
                _run_result(
                    json.dumps(
                        [
                            _open_claim_pr(42, "bug-a", "2000-01-01T00:00:00Z"),
                            _open_claim_pr(1059, "bug-a", "2000-01-01T01:00:00Z"),
                        ]
                    )
                ),
                _run_result(
                    json.dumps(
                        [
                            _open_claim_pr(2000, "bug-b", "2000-01-01T00:00:00Z"),
                            _open_claim_pr(1059, "bug-b", "2000-01-01T01:00:00Z", CLAIM_NS),
                        ]
                    )
                ),
            ]
        )

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:2] == ["git", "branch"]:
                return branch_result
            if cmd[:3] == ["gh", "pr", "list"] and "--head" in cmd:
                return next(branch_pr_results)
            if cmd[:3] == ["gh", "pr", "list"]:
                return next(open_claim_results)
            raise AssertionError(f"unexpected run: {cmd}")

        with (
            patch.object(claim_issue, "ISSUES_DIR", issues_dir),
            patch.object(sys, "argv", ["claim-issue.py", "bug-b"]),
            patch.object(claim_issue, "run", side_effect=fake_run),
            patch.object(claim_issue.time, "sleep") as mock_sleep,
            patch.object(claim_issue.time, "time_ns", return_value=CLAIM_NS),
            patch("subprocess.run") as mock_subprocess,
            pytest.raises(SystemExit, match="1"),
        ):
            claim_issue.main()

        mock_sleep.assert_not_called()
        assert mock_subprocess.call_args_list == [
            call(
                [
                    "gh",
                    "pr",
                    "edit",
                    "1059",
                    "--title",
                    "Solve: Bug B",
                    "--body",
                    f"<!-- claim: bug-b -->\n<!-- claim-ts: {CLAIM_NS} -->",
                ],
                check=True,
            ),
            call(["git", "push", "-u", "origin", "my-branch"], check=True),
        ]

    def test_existing_branch_pr_for_same_issue_is_idempotent(self, tmp_path: Path) -> None:
        issues_dir = tmp_path
        (issues_dir / "bug-a.json").write_text(json.dumps({"title": "Bug A", "priority": 1}))

        branch_result = MagicMock()
        branch_result.stdout = "my-branch\n"

        branch_pr_result = MagicMock()
        branch_pr_result.returncode = 0
        branch_pr_result.stdout = json.dumps(
            [
                {
                    "number": 42,
                    "body": _claim_body("bug-a"),
                    "url": "https://example.test/pr/42",
                }
            ]
        )

        race_result = _run_result(json.dumps([_open_claim_pr(42, "bug-a", "2000-01-01T00:00:00Z")]))

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:2] == ["git", "branch"]:
                return branch_result
            if cmd[:3] == ["gh", "pr", "list"] and "--head" in cmd:
                return branch_pr_result
            return race_result

        with (
            patch.object(claim_issue, "ISSUES_DIR", issues_dir),
            patch.object(sys, "argv", ["claim-issue.py", "bug-a"]),
            patch.object(claim_issue, "run", side_effect=fake_run),
            patch.object(claim_issue.time, "sleep") as mock_sleep,
            patch("subprocess.run") as mock_subprocess,
        ):
            claim_issue.main()

        mock_sleep.assert_called_once_with(claim_issue.RACE_SETTLE_SECONDS)
        mock_subprocess.assert_called_once_with(["git", "push", "-u", "origin", "my-branch"], check=True)

    def test_race_recheck_passes(self, tmp_path: Path) -> None:
        """Both checks return our PR as winner — claim succeeds, sleep is called."""
        issues_dir = tmp_path
        (issues_dir / "bug-a.json").write_text(json.dumps({"title": "Bug A", "priority": 1}))

        branch_result = MagicMock()
        branch_result.stdout = "my-branch\n"

        log_result = MagicMock()
        log_result.stdout = "abc123 some commit\n"

        branch_pr_result = MagicMock()
        branch_pr_result.returncode = 0
        branch_pr_result.stdout = "[]"

        create_pr_result = MagicMock()
        create_pr_result.returncode = 0
        create_pr_result.stdout = "https://example.test/pr/42\n"

        race_results = iter(
            [
                _run_result(json.dumps([_open_claim_pr(42, "bug-a", "2000-01-01T01:00:00Z", CLAIM_NS)])),
                _run_result(json.dumps([_open_claim_pr(42, "bug-a", "2000-01-01T01:00:00Z", CLAIM_NS)])),
            ]
        )

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:2] == ["git", "branch"]:
                return branch_result
            if cmd[:2] == ["git", "log"]:
                return log_result
            if cmd[:3] == ["gh", "pr", "list"] and "--head" in cmd:
                return branch_pr_result
            if cmd[:3] == ["gh", "pr", "create"]:
                return create_pr_result
            return next(race_results)

        with (
            patch.object(claim_issue, "ISSUES_DIR", issues_dir),
            patch.object(sys, "argv", ["claim-issue.py", "bug-a"]),
            patch.object(claim_issue, "run", side_effect=fake_run),
            patch.object(claim_issue.time, "sleep") as mock_sleep,
            patch.object(claim_issue.time, "time_ns", return_value=CLAIM_NS),
            patch("subprocess.run"),
        ):
            claim_issue.main()

        mock_sleep.assert_called_once_with(claim_issue.RACE_SETTLE_SECONDS)

    def test_race_recheck_fails(self, tmp_path: Path) -> None:
        """First check passes, re-check finds lower PR — exits 1."""
        issues_dir = tmp_path
        (issues_dir / "bug-a.json").write_text(json.dumps({"title": "Bug A", "priority": 1}))

        branch_result = MagicMock()
        branch_result.stdout = "my-branch\n"

        log_result = MagicMock()
        log_result.stdout = "abc123 some commit\n"

        branch_pr_result = MagicMock()
        branch_pr_result.returncode = 0
        branch_pr_result.stdout = "[]"

        create_pr_result = MagicMock()
        create_pr_result.returncode = 0
        create_pr_result.stdout = "https://example.test/pr/42\n"

        race_results = iter(
            [
                _run_result(json.dumps([_open_claim_pr(42, "bug-a", "2000-01-01T01:00:00Z", CLAIM_NS)])),
                _run_result(
                    json.dumps(
                        [
                            _open_claim_pr(41, "bug-a", "2000-01-01T00:00:00Z"),
                            _open_claim_pr(42, "bug-a", "2000-01-01T01:00:00Z", CLAIM_NS),
                        ]
                    )
                ),
            ]
        )

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:2] == ["git", "branch"]:
                return branch_result
            if cmd[:2] == ["git", "log"]:
                return log_result
            if cmd[:3] == ["gh", "pr", "list"] and "--head" in cmd:
                return branch_pr_result
            if cmd[:3] == ["gh", "pr", "create"]:
                return create_pr_result
            return next(race_results)

        with (
            patch.object(claim_issue, "ISSUES_DIR", issues_dir),
            patch.object(sys, "argv", ["claim-issue.py", "bug-a"]),
            patch.object(claim_issue, "run", side_effect=fake_run),
            patch.object(claim_issue.time, "sleep") as mock_sleep,
            patch.object(claim_issue.time, "time_ns", return_value=CLAIM_NS),
            patch("subprocess.run") as mock_subprocess,
            pytest.raises(SystemExit, match="1"),
        ):
            claim_issue.main()

        mock_sleep.assert_called_once_with(claim_issue.RACE_SETTLE_SECONDS)
        mock_subprocess.assert_called_once_with(["git", "push", "-u", "origin", "my-branch"], check=True)

    def test_race_first_check_fails_no_sleep(self, tmp_path: Path) -> None:
        """First check already finds lower PR — exits 1 without sleeping."""
        issues_dir = tmp_path
        (issues_dir / "bug-a.json").write_text(json.dumps({"title": "Bug A", "priority": 1}))

        branch_result = MagicMock()
        branch_result.stdout = "my-branch\n"

        log_result = MagicMock()
        log_result.stdout = "abc123 some commit\n"

        branch_pr_result = MagicMock()
        branch_pr_result.returncode = 0
        branch_pr_result.stdout = "[]"

        create_pr_result = MagicMock()
        create_pr_result.returncode = 0
        create_pr_result.stdout = "https://example.test/pr/42\n"

        race_result = _run_result(
            json.dumps(
                [
                    _open_claim_pr(41, "bug-a", "2000-01-01T00:00:00Z"),
                    _open_claim_pr(42, "bug-a", "2000-01-01T01:00:00Z", CLAIM_NS),
                ]
            )
        )

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:2] == ["git", "branch"]:
                return branch_result
            if cmd[:2] == ["git", "log"]:
                return log_result
            if cmd[:3] == ["gh", "pr", "list"] and "--head" in cmd:
                return branch_pr_result
            if cmd[:3] == ["gh", "pr", "create"]:
                return create_pr_result
            return race_result

        with (
            patch.object(claim_issue, "ISSUES_DIR", issues_dir),
            patch.object(sys, "argv", ["claim-issue.py", "bug-a"]),
            patch.object(claim_issue, "run", side_effect=fake_run),
            patch.object(claim_issue.time, "sleep") as mock_sleep,
            patch.object(claim_issue.time, "time_ns", return_value=CLAIM_NS),
            patch("subprocess.run") as mock_subprocess,
            pytest.raises(SystemExit, match="1"),
        ):
            claim_issue.main()

        mock_sleep.assert_not_called()
        mock_subprocess.assert_called_once_with(["git", "push", "-u", "origin", "my-branch"], check=True)


# ===========================================================================
# finalize-issue-pr
# ===========================================================================


class TestFinalizeIssuePr:
    def test_extract_claim_metadata_with_timestamp(self) -> None:
        body = "Summary\n\n<!-- claim: bug-a -->\n<!-- claim-ts: 123 -->"
        assert finalize_issue_pr.extract_claim_metadata(body) == ("<!-- claim: bug-a -->\n<!-- claim-ts: 123 -->")

    def test_extract_claim_metadata_without_timestamp(self) -> None:
        body = "Summary\n\n<!-- claim: bug-a -->"
        assert finalize_issue_pr.extract_claim_metadata(body) == "<!-- claim: bug-a -->"


# ===========================================================================
# worktree-setup
# ===========================================================================


class TestWorktreeSetup:
    def test_creates_symlinks(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        shared_images = tmp_path / "shared-images"

        with (
            patch.object(worktree_setup, "PROJECT_ROOT", project_root),
            patch.object(worktree_setup, "SHARED_IMAGES", shared_images),
            patch.object(worktree_setup, "CLIENT_MODULES", ["Mod-A"]),
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            worktree_setup.main()

        # Shared dirs created
        assert shared_images.is_dir()
        assert (tmp_path / ".m2" / "build-cache").is_dir()

        # Symlink created
        link = project_root / "Mod-A" / "plugins" / "images"
        assert link.is_symlink()
        assert link.resolve() == shared_images.resolve()

    def test_existing_dir_moved(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        shared_images = tmp_path / "shared-images"

        # Pre-existing images directory with a file
        mod_dir = project_root / "Mod-A" / "plugins" / "images"
        mod_dir.mkdir(parents=True)
        (mod_dir / "card.jpg").write_text("img data")

        with (
            patch.object(worktree_setup, "PROJECT_ROOT", project_root),
            patch.object(worktree_setup, "SHARED_IMAGES", shared_images),
            patch.object(worktree_setup, "CLIENT_MODULES", ["Mod-A"]),
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            worktree_setup.main()

        # File moved to shared location
        assert (shared_images / "card.jpg").read_text() == "img data"
        # Original replaced by symlink
        link = project_root / "Mod-A" / "plugins" / "images"
        assert link.is_symlink()

    def test_existing_symlink_untouched(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        shared_images = tmp_path / "shared-images"
        shared_images.mkdir(parents=True)

        # Pre-existing symlink
        plugins_dir = project_root / "Mod-A" / "plugins"
        plugins_dir.mkdir(parents=True)
        (plugins_dir / "images").symlink_to(shared_images)

        with (
            patch.object(worktree_setup, "PROJECT_ROOT", project_root),
            patch.object(worktree_setup, "SHARED_IMAGES", shared_images),
            patch.object(worktree_setup, "CLIENT_MODULES", ["Mod-A"]),
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            worktree_setup.main()

        link = plugins_dir / "images"
        assert link.is_symlink()
        assert link.resolve() == shared_images.resolve()


# ===========================================================================
# import-deck
# ===========================================================================


class TestImportDeck:
    def test_parse_deck_text(self) -> None:
        text = "4 Lightning Bolt\n2 Mountain\n\n1 Pyroblast\n"
        cards = import_deck.parse_deck_text(text)
        assert cards == {
            "Lightning Bolt": [(4, False)],
            "Mountain": [(2, False)],
            "Pyroblast": [(1, True)],
        }

    def test_parse_empty(self) -> None:
        assert import_deck.parse_deck_text("") == {}

    def test_format_dck(self) -> None:
        cards = {
            "Lightning Bolt": [(4, False)],
            "Mountain": [(2, False)],
            "Pyroblast": [(1, True)],
        }
        resolved = {
            "Lightning Bolt": ("A25", "141"),
            "Mountain": ("UST", "215"),
            "Pyroblast": ("ICE", "212"),
        }
        main_lines, sb_lines = import_deck.format_dck(cards, resolved)
        assert len(main_lines) == 2
        assert len(sb_lines) == 1
        assert main_lines[0] == "4 [A25:141] Lightning Bolt"
        assert sb_lines[0] == "SB: 1 [ICE:212] Pyroblast"

    def test_format_dck_unresolved_skipped(self) -> None:
        cards = {"Unknown Card": [(1, False)]}
        main_lines, sb_lines = import_deck.format_dck(cards, {})
        assert main_lines == []
        assert sb_lines == []

    def test_normalize_split_name(self) -> None:
        assert import_deck._normalize_split_name("Wear/Tear") == "Wear // Tear"
        assert import_deck._normalize_split_name("Heaven/Earth") == "Heaven // Earth"

    def test_normalize_split_name_already_canonical(self) -> None:
        assert import_deck._normalize_split_name("Wear // Tear") == "Wear // Tear"

    def test_normalize_split_name_no_slash(self) -> None:
        assert import_deck._normalize_split_name("Lightning Bolt") == "Lightning Bolt"

    def test_normalize_room_card(self) -> None:
        assert import_deck._normalize_split_name("Spiked Corridor/Torture Pit") == "Spiked Corridor // Torture Pit"

    def test_parse_deck_text_split_card(self) -> None:
        text = "1 Wear/Tear\n4 Lightning Bolt\n"
        cards = import_deck.parse_deck_text(text)
        assert "Wear/Tear" in cards
        assert cards["Wear/Tear"] == [(1, False)]

    def test_resolve_cards_normalizes_split_names(self) -> None:
        """resolve_cards normalizes slash names and keys result by original."""
        scryfall_response = {
            "data": [
                {"name": "Wear // Tear", "set": "dgm", "collector_number": "135"},
                {"name": "Lightning Bolt", "set": "a25", "collector_number": "141"},
            ],
            "not_found": [],
        }
        fake_resp = MagicMock()
        fake_resp.read.return_value = json.dumps(scryfall_response).encode()
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)

        with (
            patch.object(scryfall, "_cache", {}),
            patch.object(scryfall, "_save_cache"),
            patch("urllib.request.urlopen", return_value=fake_resp),
        ):
            resolved = import_deck.resolve_cards(["Wear/Tear", "Lightning Bolt"])

        # Keyed by original MTGGoldfish name, not Scryfall canonical name
        assert "Wear/Tear" in resolved
        assert resolved["Wear/Tear"] == ("DGM", "135")
        assert "Lightning Bolt" in resolved

    def test_resolve_cards_fallback_first_half(self) -> None:
        """Fallback queries first half of split name when collection fails."""
        collection_response = {
            "data": [],
            "not_found": [{"name": "Wear // Tear"}],
        }
        named_response = {
            "name": "Wear // Tear",
            "set": "dgm",
            "collector_number": "135",
        }
        call_count = 0

        def fake_urlopen(req):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=False)
            if call_count == 1:
                # First call: collection endpoint returns not_found
                resp.read.return_value = json.dumps(collection_response).encode()
            else:
                # Second call: named endpoint returns the card
                resp.read.return_value = json.dumps(named_response).encode()
            return resp

        with (
            patch.object(scryfall, "_cache", {}),
            patch.object(scryfall, "_save_cache"),
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            resolved = import_deck.resolve_cards(["Wear/Tear"])

        assert "Wear/Tear" in resolved
        assert resolved["Wear/Tear"] == ("DGM", "135")

    def test_format_dck_split_card(self) -> None:
        """Split cards resolved by original name appear in output."""
        cards = {"Wear/Tear": [(1, False)], "Lightning Bolt": [(4, False)]}
        resolved = {
            "Wear/Tear": ("DGM", "135"),
            "Lightning Bolt": ("A25", "141"),
        }
        main_lines, _sb_lines = import_deck.format_dck(cards, resolved)
        assert len(main_lines) == 2
        assert "1 [DGM:135] Wear/Tear" in main_lines


# ===========================================================================
# conclude-season
# ===========================================================================


class TestConcludeSeason:
    def test_main_refreshes_website_season_data(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        tournaments_dir = data_dir / "tournaments"
        website_data_dir = tmp_path / "website" / "src" / "data"
        puppeteer_dir = tmp_path / "puppeteer"
        tournaments_dir.mkdir(parents=True)
        website_data_dir.mkdir(parents=True)
        puppeteer_dir.mkdir(parents=True)

        season_file = data_dir / "season.json"
        season_file.write_text(
            json.dumps(
                {
                    "current_season": 5,
                    "phase": "regular-season",
                    "tournament": None,
                },
                indent=2,
            )
            + "\n"
        )
        # Seed the website copy with stale data to prove it gets refreshed.
        (website_data_dir / "season.json").write_text(
            json.dumps(
                {
                    "current_season": 5,
                    "phase": "regular-season",
                    "tournament": None,
                },
                indent=2,
            )
            + "\n"
        )

        benchmark_file = website_data_dir / "benchmark-results.json"
        benchmark_file.write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "modelId": f"model-{i}",
                            "modelName": f"Model {i}",
                            "rating": 1800 - i,
                            "gamesPlayed": 10 + i,
                            "reasoningEffort": "medium",
                        }
                        for i in range(8)
                    ]
                },
                indent=2,
            )
            + "\n"
        )

        presets_file = puppeteer_dir / "presets.json"
        presets_file.write_text(
            json.dumps(
                {
                    "presets": {
                        f"preset-{i}": {
                            "model": f"model-{i}",
                            "reasoning_effort": "medium",
                        }
                        for i in range(8)
                    }
                },
                indent=2,
            )
            + "\n"
        )

        personalities_file = puppeteer_dir / "personalities.json"
        personalities_file.write_text(json.dumps({f"personality-{i}": {} for i in range(8)}, indent=2) + "\n")

        with (
            patch.object(conclude_season, "_ROOT", tmp_path),
            patch.object(conclude_season, "_SEASON_FILE", season_file),
            patch.object(conclude_season, "_TOURNAMENTS_DIR", tournaments_dir),
            patch.object(conclude_season, "_BENCHMARK_RESULTS", benchmark_file),
            patch.object(conclude_season, "_PRESETS_JSON", presets_file),
            patch.object(conclude_season, "_PERSONALITIES_JSON", personalities_file),
            patch.object(conclude_season.random, "shuffle", lambda items: None),
            patch.object(sys, "argv", ["conclude_season.py", "8"]),
        ):
            assert conclude_season.main() == 0

        season_data = json.loads(season_file.read_text())
        assert season_data["phase"] == "tournament"
        assert season_data["tournament"] == "data/tournaments/season-5.json"

        website_season = json.loads((website_data_dir / "season.json").read_text())
        assert website_season == season_data

        tournament = json.loads((tournaments_dir / "season-5.json").read_text())
        assert tournament["season"] == 5
        assert tournament["size"] == 8


# ===========================================================================
# conclude-tournament
# ===========================================================================


class TestConcludeTournament:
    def test_main_advances_to_next_regular_season(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        data_dir = tmp_path / "data"
        tournaments_dir = data_dir / "tournaments"
        website_data_dir = tmp_path / "website" / "src" / "data"
        tournaments_dir.mkdir(parents=True)
        website_data_dir.mkdir(parents=True)

        season_file = data_dir / "season.json"
        season_file.write_text(
            json.dumps(
                {
                    "current_season": 3,
                    "phase": "between-seasons",
                    "tournament": "data/tournaments/season-3.json",
                },
                indent=2,
            )
            + "\n"
        )
        (website_data_dir / "season.json").write_text(
            json.dumps(
                {
                    "current_season": 3,
                    "phase": "between-seasons",
                    "tournament": "data/tournaments/season-3.json",
                },
                indent=2,
            )
            + "\n"
        )

        tournament_file = tournaments_dir / "season-3.json"
        tournament_file.write_text(
            json.dumps(
                {
                    "season": 3,
                    "size": 2,
                    "entrants": [
                        {"seed": 1, "display_name": "Alpha"},
                        {"seed": 2, "display_name": "Beta"},
                    ],
                    "rounds": [
                        {
                            "round": 1,
                            "name": "Finals",
                            "matches": [
                                {
                                    "match": 1,
                                    "seed_a": 1,
                                    "seed_b": 2,
                                    "winner_seed": 1,
                                    "games": [
                                        {"game_id": "g1", "winner_seed": 1},
                                        {"game_id": "g2", "winner_seed": 1},
                                    ],
                                }
                            ],
                        }
                    ],
                    "champion_seed": 1,
                    "completed_at": "2026-03-12T00:00:00+00:00",
                },
                indent=2,
            )
            + "\n"
        )

        with (
            patch.object(conclude_tournament, "_ROOT", tmp_path),
            patch.object(conclude_tournament, "_SEASON_FILE", season_file),
        ):
            assert conclude_tournament.main() == 0

        season_data = json.loads(season_file.read_text())
        assert season_data["current_season"] == 4
        assert season_data["phase"] == "regular-season"
        assert season_data["tournament"] is None
        website_season = json.loads((website_data_dir / "season.json").read_text())
        assert website_season == season_data

        out = capsys.readouterr().out
        assert "Season 3 champion: #1 Alpha" in out
        assert "Season 4 moved to regular season" in out

    def test_main_requires_recorded_champion(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        tournaments_dir = data_dir / "tournaments"
        tournaments_dir.mkdir(parents=True)

        season_file = data_dir / "season.json"
        season_file.write_text(
            json.dumps(
                {
                    "current_season": 1,
                    "phase": "between-seasons",
                    "tournament": "data/tournaments/season-1.json",
                },
                indent=2,
            )
            + "\n"
        )

        tournament_file = tournaments_dir / "season-1.json"
        tournament_file.write_text(
            json.dumps(
                {
                    "season": 1,
                    "size": 2,
                    "entrants": [
                        {"seed": 1, "display_name": "Alpha"},
                        {"seed": 2, "display_name": "Beta"},
                    ],
                    "rounds": [
                        {
                            "round": 1,
                            "name": "Finals",
                            "matches": [
                                {
                                    "match": 1,
                                    "seed_a": 1,
                                    "seed_b": 2,
                                    "winner_seed": 1,
                                    "games": [{"game_id": "g1", "winner_seed": 1}],
                                }
                            ],
                        }
                    ],
                },
                indent=2,
            )
            + "\n"
        )

        with (
            patch.object(conclude_tournament, "_ROOT", tmp_path),
            patch.object(conclude_tournament, "_SEASON_FILE", season_file),
            pytest.raises(AssertionError, match="champion has not been recorded"),
        ):
            conclude_tournament.main()


# ===========================================================================
# import-metagame
# ===========================================================================


class TestImportMetagame:
    def test_clean_archetype_name_uuid(self) -> None:
        assert (
            import_metagame.clean_archetype_name("4c-reanimator-70c5fc5f-0149-4242-8b1c-dd0b72eeb297")
            == "4c-reanimator"
        )

    def test_clean_archetype_name_numeric(self) -> None:
        assert import_metagame.clean_archetype_name("death-s-shadow-472") == "death-s-shadow"

    def test_clean_archetype_name_noop(self) -> None:
        assert import_metagame.clean_archetype_name("sneak-and-show") == "sneak-and-show"

    def test_slug_to_title_case(self) -> None:
        assert import_metagame.slug_to_title_case("sneak-and-show") == "Sneak-And-Show"
        assert import_metagame.slug_to_title_case("4c-reanimator") == "4c-Reanimator"


# ===========================================================================
# game-gz-bootstrap
# ===========================================================================


class TestGameGzBootstrap:
    def test_bootstraps_from_shared_logs_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        game_id = "game_20260314_111422_g1"
        games_dir = tmp_path / "website" / "public" / "games"
        logs_dir = tmp_path / ".mage-bench" / "logs"
        game_dir = logs_dir / game_id
        export_path = games_dir / f"{game_id}.json"
        export_data = {
            "version": 7,
            "id": game_id,
            "timestamp": "2026-03-14T11:14:22-07:00",
            "gameType": "Two Player Duel",
            "deckType": "jumpstart",
            "totalTurns": 7,
            "winner": "Alice",
            "harnessEpoch": 1,
            "youtubeUrl": "",
            "players": [
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "model-a",
                    "totalCostUsd": 0.25,
                    "toolCallsOk": 0,
                    "toolCallsFailed": 0,
                    "thinkingTimeSecs": 0.0,
                },
                {
                    "name": "Bob",
                    "type": "pilot",
                    "model": "model-b",
                    "totalCostUsd": 0.0,
                    "toolCallsOk": 0,
                    "toolCallsFailed": 0,
                    "thinkingTimeSecs": 0.0,
                },
            ],
            "cardImages": {},
            "snapshots": [],
            "actions": [],
            "llmEvents": [],
            "gameOver": None,
            "annotations": [],
            "blunderScriptVersion": 0,
            "season": 1,
            "tournament": None,
        }
        games_dir.mkdir(parents=True)
        game_dir.mkdir(parents=True)
        (game_dir / "game_events.jsonl").write_text("{}\n")

        export_path.write_text(json.dumps(export_data))

        def fake_run(cmd: list[str], check: bool) -> MagicMock:
            assert cmd == ["uv", "run", "python", "scripts/export_game.py", game_id]
            assert check is True
            export_path.write_text(json.dumps(export_data))
            return MagicMock()

        export_path.unlink()
        with (
            patch.object(game_gz_bootstrap, "GAMES_DIR", games_dir),
            patch.object(game_gz_bootstrap, "LOGS_DIR", logs_dir),
            patch.object(game_gz_bootstrap.subprocess, "run", side_effect=fake_run) as mock_run,
        ):
            game_gz_bootstrap.main(game_id)

        mock_run.assert_called_once()
        out = capsys.readouterr().out
        assert f"Game: {game_id} | jumpstart | 7 turns | Winner: Alice" in out

    def test_failed_tool_call_detection_requires_explicit_errors(self) -> None:
        events = [
            {
                "type": "tool_call",
                "player": "Alice",
                "tool": "get_action_choices",
                "result": json.dumps({"required": True, "action_pending": True}),
            },
            {
                "type": "tool_call",
                "player": "Alice",
                "tool": "choose_action",
                "result": json.dumps(
                    {
                        "success": True,
                        "failed": [{"id": "p1", "reason": "not a valid attacker"}],
                    }
                ),
            },
            {
                "type": "tool_call",
                "player": "Alice",
                "tool": "choose_action",
                "result": json.dumps(
                    {"success": False, "error": "Index 0 out of range (call get_action_choices first)"}
                ),
            },
            {
                "type": "tool_call",
                "player": "Bob",
                "tool": "send_chat_message",
                "result": json.dumps({"error": "Missing required 'message' parameter"}),
            },
        ]

        failures = game_gz_bootstrap._failed_tool_calls(events)

        assert [event["tool"] for event in failures] == [
            "choose_action",
            "send_chat_message",
        ]
