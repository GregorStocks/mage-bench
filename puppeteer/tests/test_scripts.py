"""Tests for scripts/ Python rewrites."""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts import scryfall

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"


def _import_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


list_issues = _import_script("list-issues")
claim_issue = _import_script("claim-issue")
worktree_setup = _import_script("worktree-setup")
import_deck = _import_script("import-deck")
import_metagame = _import_script("import-metagame")
conclude_tournament = _import_script("conclude_tournament")


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
                    "body": "<!-- claim: bug-b -->",
                    "url": "https://example.test/pr/1059",
                }
            ]
        )

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:2] == ["git", "branch"]:
                return branch_result
            if cmd[:3] == ["gh", "pr", "list"] and "--head" in cmd:
                return branch_pr_result
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
                    "body": "<!-- claim: bug-a -->",
                    "url": "https://example.test/pr/42",
                }
            ]
        )

        race_result = MagicMock()
        race_result.stdout = "42\n"

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

        race_result = MagicMock()
        race_result.stdout = "42\n"

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:2] == ["git", "branch"]:
                return branch_result
            if cmd[:2] == ["git", "log"]:
                return log_result
            if cmd[:3] == ["gh", "pr", "list"] and "--head" in cmd:
                return branch_pr_result
            if cmd[:3] == ["gh", "pr", "create"]:
                return create_pr_result
            # _race_winner calls
            return race_result

        with (
            patch.object(claim_issue, "ISSUES_DIR", issues_dir),
            patch.object(sys, "argv", ["claim-issue.py", "bug-a"]),
            patch.object(claim_issue, "run", side_effect=fake_run),
            patch.object(claim_issue.time, "sleep") as mock_sleep,
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
                MagicMock(stdout="42\n"),  # first check: we win
                MagicMock(stdout="41\n"),  # re-check: lower PR appeared
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
            patch("subprocess.run"),
            pytest.raises(SystemExit, match="1"),
        ):
            claim_issue.main()

        mock_sleep.assert_called_once_with(claim_issue.RACE_SETTLE_SECONDS)

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

        race_result = MagicMock()
        race_result.stdout = "41\n"  # lower PR already claims it

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
            patch("subprocess.run"),
            pytest.raises(SystemExit, match="1"),
        ):
            claim_issue.main()

        mock_sleep.assert_not_called()


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
# conclude-tournament
# ===========================================================================


class TestConcludeTournament:
    def test_main_advances_to_next_regular_season(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        data_dir = tmp_path / "data"
        tournaments_dir = data_dir / "tournaments"
        tournaments_dir.mkdir(parents=True)

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
