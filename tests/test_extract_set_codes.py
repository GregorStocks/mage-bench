"""Tests for extract_golden_set_codes in conftest."""

from pathlib import Path
from unittest.mock import patch

from tests.conftest import extract_golden_set_codes


def test_extract_set_codes_from_deck_files(tmp_path: Path) -> None:
    """Extracts set codes from .dck files in tests/decks/ and legacy paths."""
    decks_dir = tmp_path / "tests" / "decks"
    decks_dir.mkdir(parents=True)

    # Write a test deck file
    (decks_dir / "test.dck").write_text("1 [KLD:253] Island\n1 [M13:45] Clone\n1 [SOM:174] Memnite\n")

    # Write a legacy deck
    legacy_dir = tmp_path / "Mage.Client" / "release" / "sample-decks" / "Legacy"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "Red-Stompy.dck").write_text("4 [A25:148] Chalice of the Void\n")

    with (
        patch(
            "tests.conftest.DECK_RED_STOMPY",
            "Mage.Client/release/sample-decks/Legacy/Red-Stompy.dck",
        ),
        patch(
            "tests.conftest.DECK_GOBLINS",
            "Mage.Client/release/sample-decks/Legacy/nonexistent.dck",
        ),
    ):
        codes = extract_golden_set_codes(tmp_path)

    code_set = set(codes.split(","))
    assert code_set == {"A25", "KLD", "M13", "SOM"}


def test_extract_set_codes_on_real_repo() -> None:
    """Smoke test: extract_golden_set_codes on the real repo returns non-empty codes."""
    project_root = Path(__file__).resolve().parent.parent
    codes = extract_golden_set_codes(project_root)
    code_set = set(codes.split(","))
    # Should find at least 10 set codes from the real deck files
    assert len(code_set) >= 10
    # Known set codes that must be present (from clone_and_memnite.dck)
    assert "KLD" in code_set
    assert "M13" in code_set
    assert "SOM" in code_set
