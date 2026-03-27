"""Tests for migrated magebench CLI modules."""

import gzip
import importlib
import json
import sys
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from magebench.common import http_utils
from magebench.game import scryfall
from magebench.game.game_export_types import ToolCallEvent

worktree_setup = importlib.import_module("magebench.cli.worktree_setup")
import_deck = importlib.import_module("magebench.cli.import_deck")
import_metagame = importlib.import_module("magebench.cli.import_metagame")
conclude_season = importlib.import_module("magebench.cli.conclude_season")
conclude_tournament = importlib.import_module("magebench.cli.conclude_tournament")
game_gz_bootstrap = importlib.import_module("magebench.cli.game_gz_bootstrap")
find_test_cards = importlib.import_module("magebench.cli.find_test_cards")


# ===========================================================================
# http_utils
# ===========================================================================


class TestHttpUtils:
    def test_fetch_https_bytes_rejects_non_https_scheme(self) -> None:
        with pytest.raises(AssertionError, match="Expected https URL"):
            http_utils.fetch_https_bytes(
                "http://api.scryfall.com/cards/search?q=bolt",
                allowed_hosts={"api.scryfall.com"},
            )

    def test_fetch_https_bytes_rejects_unexpected_host(self) -> None:
        with pytest.raises(AssertionError, match="Unexpected HTTPS host"):
            http_utils.fetch_https_bytes(
                "https://example.com/cards/search?q=bolt",
                allowed_hosts={"api.scryfall.com"},
            )

    def test_fetch_https_bytes_passes_headers_body_and_timeout(self) -> None:
        opener = MagicMock()
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = None
        response.read.return_value = b"{}"
        opener.open.return_value = response

        with patch.object(http_utils.urllib.request, "build_opener", return_value=opener):
            body = http_utils.fetch_https_bytes(
                "https://api.scryfall.com/cards/search?q=bolt",
                allowed_hosts={"api.scryfall.com"},
                data=b"payload",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=3.5,
            )

        assert body == b"{}"
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        assert isinstance(request, urllib.request.Request)
        assert request.full_url == "https://api.scryfall.com/cards/search?q=bolt"
        assert request.data == b"payload"
        assert request.get_header("Accept") == "application/json"
        assert request.get_header("Content-type") == "application/json"
        assert opener.open.call_args.kwargs == {"timeout": 3.5}

    def test_redirect_handler_rejects_unexpected_host(self) -> None:
        handler = http_utils._ValidatedHttpsRedirectHandler(allowed_hosts=frozenset({"api.scryfall.com"}))
        req = urllib.request.Request("https://api.scryfall.com/cards/search?q=bolt")

        with pytest.raises(AssertionError, match="Unexpected HTTPS host"):
            handler.redirect_request(
                req,
                None,
                302,
                "Found",
                {},
                "https://evil.example/cards/search?q=bolt",
            )


# ===========================================================================
# worktree_setup
# ===========================================================================


class TestWorktreeSetup:
    def _setup_project(self, tmp_path: Path) -> Path:
        """Create a project root with .mvn/ dir (as exists in real repos)."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / ".mvn").mkdir()
        return project_root

    @contextmanager
    def _patches(
        self,
        project_root: Path,
        tmp_path: Path,
        shared_images: Path,
        main_worktree_root: Path | None = None,
    ):
        """Common patches for worktree_setup tests."""
        with (
            patch.object(worktree_setup, "PROJECT_ROOT", project_root),
            patch.object(worktree_setup, "SHARED_IMAGES", shared_images),
            patch.object(worktree_setup, "CLIENT_MODULES", ["Mod-A"]),
            patch("pathlib.Path.home", return_value=tmp_path),
            patch.object(
                worktree_setup,
                "_find_main_worktree_root",
                return_value=main_worktree_root,
            ),
        ):
            yield

    def test_creates_symlinks_and_maven_config(self, tmp_path: Path) -> None:
        project_root = self._setup_project(tmp_path)
        shared_images = tmp_path / "shared-images"

        with self._patches(project_root, tmp_path, shared_images):
            worktree_setup.main()

        # Shared dirs created
        assert shared_images.is_dir()
        assert (tmp_path / ".m2" / "build-cache").is_dir()

        # Symlink created
        link = project_root / "Mod-A" / "plugins" / "images"
        assert link.is_symlink()
        assert link.resolve() == shared_images.resolve()

        # Per-worktree Maven local repo created
        assert (project_root / ".m2-repo").is_dir()

        # maven.config written with absolute path
        maven_config = project_root / ".mvn" / "maven.config"
        assert maven_config.exists()
        content = maven_config.read_text()
        assert content.startswith("-Dmaven.repo.local=")
        assert str(project_root.resolve() / ".m2-repo") in content

    def test_existing_dir_moved(self, tmp_path: Path) -> None:
        project_root = self._setup_project(tmp_path)
        shared_images = tmp_path / "shared-images"

        # Pre-existing images directory with a file
        mod_dir = project_root / "Mod-A" / "plugins" / "images"
        mod_dir.mkdir(parents=True)
        (mod_dir / "card.jpg").write_text("img data")

        with self._patches(project_root, tmp_path, shared_images):
            worktree_setup.main()

        # File moved to shared location
        assert (shared_images / "card.jpg").read_text() == "img data"
        # Original replaced by symlink
        link = project_root / "Mod-A" / "plugins" / "images"
        assert link.is_symlink()

    def test_existing_symlink_untouched(self, tmp_path: Path) -> None:
        project_root = self._setup_project(tmp_path)
        shared_images = tmp_path / "shared-images"
        shared_images.mkdir(parents=True)

        # Pre-existing symlink
        plugins_dir = project_root / "Mod-A" / "plugins"
        plugins_dir.mkdir(parents=True)
        (plugins_dir / "images").symlink_to(shared_images)

        with self._patches(project_root, tmp_path, shared_images):
            worktree_setup.main()

        link = plugins_dir / "images"
        assert link.is_symlink()
        assert link.resolve() == shared_images.resolve()

    def test_seeds_m2_repo_from_main_worktree(self, tmp_path: Path) -> None:
        project_root = self._setup_project(tmp_path)
        shared_images = tmp_path / "shared-images"

        # Simulate a main worktree with a populated .m2-repo
        main_root = tmp_path / "main-worktree"
        main_root.mkdir()
        main_m2 = main_root / ".m2-repo"
        main_m2.mkdir()
        (main_m2 / "org").mkdir()
        (main_m2 / "org" / "example.jar").write_text("artifact")

        with self._patches(project_root, tmp_path, shared_images, main_worktree_root=main_root):
            worktree_setup.main()

        # .m2-repo seeded from main worktree
        seeded_jar = project_root / ".m2-repo" / "org" / "example.jar"
        assert seeded_jar.exists()
        assert seeded_jar.read_text() == "artifact"

    def test_skips_seed_when_m2_repo_exists(self, tmp_path: Path) -> None:
        project_root = self._setup_project(tmp_path)
        shared_images = tmp_path / "shared-images"

        # Pre-existing .m2-repo with different content
        m2_repo = project_root / ".m2-repo"
        m2_repo.mkdir()
        (m2_repo / "existing.txt").write_text("keep me")

        # Main worktree has .m2-repo too
        main_root = tmp_path / "main-worktree"
        main_root.mkdir()
        main_m2 = main_root / ".m2-repo"
        main_m2.mkdir()
        (main_m2 / "other.jar").write_text("other")

        with self._patches(project_root, tmp_path, shared_images, main_worktree_root=main_root):
            worktree_setup.main()

        # Existing content preserved, no seed overwrite
        assert (m2_repo / "existing.txt").read_text() == "keep me"
        assert not (m2_repo / "other.jar").exists()

    def test_seeds_from_global_m2_when_no_main_worktree(self, tmp_path: Path) -> None:
        project_root = self._setup_project(tmp_path)
        shared_images = tmp_path / "shared-images"

        # Simulate ~/.m2/repository with a dep
        global_m2 = tmp_path / ".m2" / "repository"
        global_m2.mkdir(parents=True)
        (global_m2 / "guava.jar").write_text("dep")

        with self._patches(project_root, tmp_path, shared_images):
            worktree_setup.main()

        # .m2-repo seeded from ~/.m2/repository
        assert (project_root / ".m2-repo" / "guava.jar").read_text() == "dep"


# ===========================================================================
# import_deck
# ===========================================================================


class TestImportDeck:
    def test_download_deck_text_uses_validated_https_fetch(self) -> None:
        with patch.object(http_utils, "fetch_https_bytes", return_value=b"4 Lightning Bolt\n") as mock_fetch:
            deck_text = import_deck.download_deck_text("https://www.mtggoldfish.com/deck/7616949")

        assert deck_text == "4 Lightning Bolt\n"
        mock_fetch.assert_called_once_with(
            "https://www.mtggoldfish.com/deck/download/7616949",
            allowed_hosts=import_deck._MTGGOLDFISH_HOSTS,
        )

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
            patch.object(
                http_utils,
                "fetch_https_bytes",
                return_value=fake_resp.read.return_value,
            ),
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

        def fake_fetch(url: str, **_kwargs: object) -> bytes:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: collection endpoint returns not_found
                assert url.startswith("https://api.scryfall.com/cards/collection")
                return json.dumps(collection_response).encode()
            assert url.startswith("https://api.scryfall.com/cards/named?")
            # Second call: named endpoint returns the card
            return json.dumps(named_response).encode()

        with (
            patch.object(scryfall, "_cache", {}),
            patch.object(scryfall, "_save_cache"),
            patch.object(http_utils, "fetch_https_bytes", side_effect=fake_fetch),
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
# find_test_cards
# ===========================================================================


class TestFindTestCards:
    def test_build_query_appends_filter(self) -> None:
        recipe = find_test_cards.RECIPE_BY_NAME["clone-effect"]

        query = find_test_cards.build_query(
            recipe=recipe,
            raw_query=None,
            extra_filter="t:blue",
        )

        assert query.endswith("t:blue")
        assert "function:clone" in query

    def test_oracle_summary_joins_faces(self) -> None:
        card = {
            "name": "Boggart Trawler // Boggart Bog",
            "mana_cost": "",
            "type_line": "",
            "set": "dsk",
            "collector_number": "75",
            "card_faces": [
                {
                    "name": "Boggart Trawler",
                    "mana_cost": "{2}{B}",
                    "type_line": "Creature — Goblin",
                    "oracle_text": "When this enters, mill three cards.",
                },
                {
                    "name": "Boggart Bog",
                    "mana_cost": "",
                    "type_line": "Land",
                    "oracle_text": "As this enters, you may pay 3 life.\nIf you don't, it enters tapped.",
                },
            ],
        }

        summary = find_test_cards.oracle_summary(card)

        assert "Boggart Trawler {2}{B} -- Creature" in summary
        assert "Boggart Bog -- Land" in summary
        assert "If you don't, it enters tapped." in summary

    def test_main_lists_recipes(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch.object(sys, "argv", ["find_test_cards.py", "--list-recipes"]):
            find_test_cards.main()

        out = capsys.readouterr().out
        assert "free-mana" in out
        assert "clone-effect" in out
        assert "function:clone" in out

    def test_main_prints_search_results(self, capsys: pytest.CaptureFixture[str]) -> None:
        results = [
            {
                "name": "Memnite",
                "mana_cost": "{0}",
                "type_line": "Artifact Creature — Construct",
                "oracle_text": "",
                "set": "som",
                "collector_number": "174",
            },
            {
                "name": "Ornithopter",
                "mana_cost": "{0}",
                "type_line": "Artifact Creature — Thopter",
                "oracle_text": "Flying",
                "set": "m10",
                "collector_number": "216",
            },
        ]

        with (
            patch.object(find_test_cards.scryfall, "search", return_value=results),
            patch.object(
                sys,
                "argv",
                [
                    "find_test_cards.py",
                    "--recipe",
                    "zero-mana-body",
                    "--limit",
                    "2",
                ],
            ),
        ):
            find_test_cards.main()

        out = capsys.readouterr().out
        assert "Recipe: zero-mana-body" in out
        assert "Query: game:paper unique:cards" in out
        assert "1. Memnite {0}" in out
        assert "2. Ornithopter {0}" in out
        assert "Flying" in out

    def test_main_exits_when_no_cards_found(self) -> None:
        with (
            patch.object(find_test_cards.scryfall, "search", return_value=[]),
            patch.object(
                sys,
                "argv",
                ["find_test_cards.py", "--query", "game:paper unique:cards t:artifact"],
            ),
            pytest.raises(SystemExit, match="No cards found for query"),
        ):
            find_test_cards.main()

    def test_format_card_fails_fast_on_missing_required_field(self) -> None:
        with pytest.raises(AssertionError, match="missing required field: set"):
            find_test_cards.format_card(
                {
                    "name": "Memnite",
                    "mana_cost": "{0}",
                    "type_line": "Artifact Creature — Construct",
                    "oracle_text": "",
                    "collector_number": "174",
                },
                1,
            )


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
            patch.object(conclude_season.random, "shuffle", lambda _items: None),
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
# import_metagame
# ===========================================================================


class TestImportMetagame:
    def test_fetch_archetype_urls_uses_validated_https_fetch(self) -> None:
        html = """
            <a href="/archetype/legacy-death-s-shadow">Shadow</a>
            <a href="/archetype/legacy-sneak-and-show">Sneak</a>
            <a href="/archetype/legacy-death-s-shadow">Shadow again</a>
        """
        with patch.object(http_utils, "fetch_https_text", return_value=html) as mock_fetch:
            urls = import_metagame.fetch_archetype_urls("legacy", 5)

        assert urls == [
            "/archetype/legacy-death-s-shadow",
            "/archetype/legacy-sneak-and-show",
        ]
        mock_fetch.assert_called_once_with(
            "https://www.mtggoldfish.com/metagame/legacy/full#paper",
            allowed_hosts=import_metagame._MTGGOLDFISH_HOSTS,
        )

    def test_get_deck_id_uses_validated_https_fetch(self) -> None:
        html = '<a href="/deck/7616949">Deck</a>'
        with patch.object(http_utils, "fetch_https_text", return_value=html) as mock_fetch:
            deck_id = import_metagame.get_deck_id("https://www.mtggoldfish.com/archetype/legacy-death-s-shadow#paper")

        assert deck_id == "7616949"
        mock_fetch.assert_called_once_with(
            "https://www.mtggoldfish.com/archetype/legacy-death-s-shadow#paper",
            allowed_hosts=import_metagame._MTGGOLDFISH_HOSTS,
        )

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
# game_gz_bootstrap
# ===========================================================================


class TestGameGzBootstrap:
    def test_bootstraps_from_shared_logs_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        game_id = "game_20260314_111422_g1"
        games_dir = tmp_path / "website" / "public" / "games"
        logs_dir = tmp_path / ".mage-bench" / "logs"
        game_dir = logs_dir / game_id
        export_path = games_dir / f"{game_id}.json5"
        export_data = {
            "version": 9,
            "id": game_id,
            "timestamp": "2026-03-14T11:14:22-07:00",
            "game_type": "Two Player Duel",
            "deck_type": "jumpstart",
            "total_turns": 7,
            "winner": "Alice",
            "harness_epoch": 1,
            "youtube_url": "",
            "players": [
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "model-a",
                    "total_cost_usd": 0.25,
                    "tool_calls_ok": 0,
                    "tool_calls_failed": 0,
                    "thinking_time_secs": 0.0,
                },
                {
                    "name": "Bob",
                    "type": "pilot",
                    "model": "model-b",
                    "total_cost_usd": 0.0,
                    "tool_calls_ok": 0,
                    "tool_calls_failed": 0,
                    "thinking_time_secs": 0.0,
                },
            ],
            "card_images": {},
            "snapshots": [],
            "actions": [],
            "llm_events": [],
            "game_over": None,
            "annotations": [],
            "blunder_script_version": 0,
            "season": 1,
            "tournament": None,
        }
        games_dir.mkdir(parents=True)
        game_dir.mkdir(parents=True)
        (game_dir / "game_events.jsonl").write_text("{}\n")

        export_path.write_text(json.dumps(export_data))

        def fake_run(cmd: list[str], *, check: bool) -> MagicMock:
            assert cmd == ["uv", "run", "python", "-m", "magebench.cli.export_game", game_id]
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

    def test_prefers_existing_json5_gz_export_without_reexport(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        game_id = "game_20260314_111422_g2"
        games_dir = tmp_path / "website" / "public" / "games"
        logs_dir = tmp_path / ".mage-bench" / "logs"
        export_path = games_dir / f"{game_id}.json5.gz"
        export_data = {
            "version": 9,
            "id": game_id,
            "timestamp": "2026-03-14T11:14:22-07:00",
            "game_type": "Two Player Duel",
            "deck_type": "jumpstart",
            "total_turns": 9,
            "winner": "Bob",
            "harness_epoch": 1,
            "youtube_url": "",
            "players": [
                {
                    "name": "Alice",
                    "type": "pilot",
                    "model": "model-a",
                    "total_cost_usd": 0.0,
                    "tool_calls_ok": 0,
                    "tool_calls_failed": 0,
                    "thinking_time_secs": 0.0,
                },
                {
                    "name": "Bob",
                    "type": "pilot",
                    "model": "model-b",
                    "total_cost_usd": 0.1,
                    "tool_calls_ok": 0,
                    "tool_calls_failed": 0,
                    "thinking_time_secs": 0.0,
                },
            ],
            "card_images": {},
            "snapshots": [],
            "actions": [],
            "llm_events": [],
            "game_over": None,
            "annotations": [],
            "blunder_script_version": 0,
            "season": 1,
            "tournament": None,
        }
        games_dir.mkdir(parents=True)
        logs_dir.mkdir(parents=True)
        export_path.write_bytes(gzip.compress(json.dumps(export_data).encode()))

        with (
            patch.object(game_gz_bootstrap, "GAMES_DIR", games_dir),
            patch.object(game_gz_bootstrap, "LOGS_DIR", logs_dir),
            patch.object(game_gz_bootstrap.subprocess, "run") as mock_run,
        ):
            game_gz_bootstrap.main(game_id)

        mock_run.assert_not_called()
        out = capsys.readouterr().out
        assert f"Game: {game_id} | jumpstart | 9 turns | Winner: Bob" in out

    def test_failed_tool_call_detection_requires_explicit_errors(self) -> None:
        events = [
            ToolCallEvent(
                type="tool_call",
                player="Alice",
                tool="get_action_choices",
                args={},
                result=json.dumps({"required": True, "action_pending": True}),
            ),
            ToolCallEvent(
                type="tool_call",
                player="Alice",
                tool="choose_action",
                args={},
                result=json.dumps(
                    {
                        "success": True,
                        "failed": [{"id": "p1", "reason": "not a valid attacker"}],
                    }
                ),
            ),
            ToolCallEvent(
                type="tool_call",
                player="Alice",
                tool="choose_action",
                args={},
                result=json.dumps(
                    {
                        "success": False,
                        "error": "Index 0 out of range (call get_action_choices first)",
                    }
                ),
            ),
            ToolCallEvent(
                type="tool_call",
                player="Bob",
                tool="send_chat_message",
                args={},
                result=json.dumps({"error": "Missing required 'message' parameter"}),
            ),
        ]

        failures = game_gz_bootstrap._failed_tool_calls(events)

        assert [event.tool for event in failures] == [
            "choose_action",
            "send_chat_message",
        ]
