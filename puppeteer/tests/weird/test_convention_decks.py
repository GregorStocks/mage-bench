"""Convention tests for checked-in deck directories."""

import json

from tests.weird.repo_convention_helpers import DECKS_DIR, EXPECTED_DECK_FORMATS


class TestNoOrphanedDecks:
    def test_all_decks_referenced_by_registry(self) -> None:
        """Every deck JSON in data/decks/{format}/ should exist and be loadable.

        Since the registry IS the deck files (each .json file is a
        self-contained deck definition), this test ensures no deck file is
        broken or empty — an orphan would be a file that isn't valid JSON
        with the required fields.
        """
        format_dirs = ["standard", "modern", "legacy", "commander"]

        for deck_format in format_dirs:
            format_dir = DECKS_DIR / deck_format
            if not format_dir.is_dir():
                continue
            deck_files = list(format_dir.glob("*.json"))
            assert deck_files, f"No deck files in {deck_format}/"
            for path in deck_files:
                data = json.loads(path.read_text())
                assert "name" in data, f"{deck_format}/{path.name} missing 'name'"
                assert "cards" in data, f"{deck_format}/{path.name} missing 'cards'"
                assert data["cards"], f"{deck_format}/{path.name} has empty cards list"


class TestDeckFormatDirectories:
    def test_no_unexpected_format_dirs(self) -> None:
        """Subdirectories under data/decks/ must be in the expected set — catches typos like 'standrard'."""
        actual = {path.name for path in DECKS_DIR.iterdir() if path.is_dir()}
        unexpected = actual - EXPECTED_DECK_FORMATS
        assert not unexpected, (
            f"Unexpected deck format directories (typo?): {sorted(unexpected)}. "
            f"If intentional, add to EXPECTED_DECK_FORMATS."
        )

    def test_all_expected_formats_exist(self) -> None:
        """Every expected format directory should exist and contain decks."""
        for deck_format in sorted(EXPECTED_DECK_FORMATS):
            format_dir = DECKS_DIR / deck_format
            assert format_dir.is_dir(), f"Expected deck format directory missing: {deck_format}/"
