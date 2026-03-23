"""Tests for magebench.game.jumpstart."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from magebench.game.jumpstart import (
    Card,
    HalfDeck,
    _parse_dck_card,
    create_random_jumpstart_deck,
    generate_dck,
    load_jumpstart_themes,
)

# --- Sample JSON data ---

CATS_THEME = {
    "name": "Cats",
    "strategy": "",
    "variants": [
        {
            "cards": [
                "1 [JMP:1] Savannah Lions",
                "1 [JMP:2] King of the Pride",
                "1 [JMP:3] Regal Caracal",
                "1 [JMP:4] Leonin Snarecaster",
                "1 [JMP:5] Felidar Cub",
                "1 [JMP:6] Basri's Acolyte",
                "1 [JMP:7] Skyhunter Patrol",
                "1 [JMP:8] Make a Stand",
                "1 [JMP:9] Impeccable Timing",
                "1 [JMP:10] Weight of Conscience",
                "1 [JMP:11] Leonin Scimitar",
                "1 [JMP:12] Ingenious Leonin",
                "1 [JMP:30] Thriving Heath",
                "7 [JMP:50] Plains",
            ]
        },
        {
            "cards": [
                "1 [JMP:1] Savannah Lions",
                "1 [JMP:2] King of the Pride",
                "1 [JMP:13] Ajani's Pridemate",
                "1 [JMP:4] Leonin Snarecaster",
                "1 [JMP:14] Trained Caracal",
                "1 [JMP:6] Basri's Acolyte",
                "1 [JMP:15] Savannah Sage",
                "1 [JMP:8] Make a Stand",
                "1 [JMP:9] Impeccable Timing",
                "1 [JMP:10] Weight of Conscience",
                "1 [JMP:11] Leonin Scimitar",
                "1 [JMP:12] Ingenious Leonin",
                "1 [JMP:30] Thriving Heath",
                "7 [JMP:50] Plains",
            ]
        },
    ],
}

DOGS_THEME = {
    "name": "Dogs",
    "strategy": "",
    "variants": [
        {
            "cards": [
                "1 [JMP:100] Alpine Watchdog",
                "1 [JMP:101] Pack Leader",
                "1 [JMP:102] Rambunctious Mutt",
                "1 [JMP:103] Selfless Savior",
                "1 [JMP:104] Bolt Hound",
                "1 [JMP:105] Release the Dogs",
                "1 [JMP:106] Trusted Watchdog",
                "1 [JMP:107] Resolute Watchdog",
                "1 [JMP:108] Ferocious Pup",
                "1 [JMP:109] Isamaru, Hound of Konda",
                "1 [JMP:110] Collar the Culprit",
                "1 [JMP:111] Angelic Gift",
                "1 [JMP:30] Thriving Heath",
                "7 [JMP:50] Plains",
            ]
        }
    ],
}

LIGHTNING_THEME = {
    "name": "Lightning",
    "strategy": "",
    "variants": [
        {
            "cards": [
                "1 [JMP:200] Lightning Bolt",
                "1 [JMP:201] Chain Lightning",
                "1 [JMP:202] Ball Lightning",
                "1 [JMP:203] Lightning Elemental",
                "1 [JMP:204] Goblin Electromancer",
                "1 [JMP:205] Thermo-Alchemist",
                "1 [JMP:206] Viashino Pyromancer",
                "1 [JMP:207] Shock",
                "1 [JMP:208] Searing Spear",
                "1 [JMP:209] Lightning Strike",
                "1 [JMP:210] Incinerate",
                "1 [JMP:211] Firebolt",
                "1 [JMP:35] Thriving Bluff",
                "7 [JMP:64] Mountain",
            ]
        }
    ],
}


def _make_jumpstart_registry(root: Path) -> None:
    """Create a jumpstart registry directory with sample JSON files."""
    reg_dir = root / "data" / "decks" / "jumpstart"
    reg_dir.mkdir(parents=True)
    (reg_dir / "cats.json").write_text(json.dumps(CATS_THEME))
    (reg_dir / "dogs.json").write_text(json.dumps(DOGS_THEME))
    (reg_dir / "lightning.json").write_text(json.dumps(LIGHTNING_THEME))


# --- _parse_dck_card ---


def test_parse_dck_card() -> None:
    card = _parse_dck_card("4 [M21:123] Lightning Bolt")
    assert card.count == 4
    assert card.set_code == "M21"
    assert card.collector_number == "123"
    assert card.name == "Lightning Bolt"


def test_parse_dck_card_multiword() -> None:
    card = _parse_dck_card("1 [JMP:109] Isamaru, Hound of Konda")
    assert card.count == 1
    assert card.name == "Isamaru, Hound of Konda"


# --- load_jumpstart_themes ---


def test_load_jumpstart_themes() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_jumpstart_registry(root)
        half_decks = load_jumpstart_themes(root)

    # One representative per theme (first variant)
    assert len(half_decks) == 3
    themes = {hd.theme for hd in half_decks}
    assert themes == {"Cats", "Dogs", "Lightning"}

    cats = next(hd for hd in half_decks if hd.theme == "Cats")
    assert cats.variant == 0
    assert cats.card_count == 20


def test_load_jumpstart_themes_sorted() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_jumpstart_registry(root)
        half_decks = load_jumpstart_themes(root)

    # Sorted by filename (cats.json, dogs.json, lightning.json)
    assert half_decks[0].theme == "Cats"
    assert half_decks[1].theme == "Dogs"
    assert half_decks[2].theme == "Lightning"


# --- generate_dck ---


def test_generate_dck() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_jumpstart_registry(root)
        half_decks = load_jumpstart_themes(root)

    cats = next(hd for hd in half_decks if hd.theme == "Cats")
    dogs = next(hd for hd in half_decks if hd.theme == "Dogs")

    dck = generate_dck(cats, dogs)
    assert dck.startswith("NAME:Cats + Dogs\n")
    lines = dck.strip().split("\n")
    assert len(lines) > 1
    # Should contain cards from both halves
    assert any("Savannah Lions" in line for line in lines)
    assert any("Alpine Watchdog" in line for line in lines)


def test_generate_dck_cross_color() -> None:
    """Half-decks from different colors can be combined."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_jumpstart_registry(root)
        half_decks = load_jumpstart_themes(root)

    cats = next(hd for hd in half_decks if hd.theme == "Cats")
    lightning = next(hd for hd in half_decks if hd.theme == "Lightning")

    dck = generate_dck(cats, lightning)
    assert "Cats + Lightning" in dck or "Lightning + Cats" in dck
    # Both Plains and Mountain should appear
    assert "Plains" in dck
    assert "Mountain" in dck


# --- HalfDeck ---


def test_half_deck_full_name() -> None:
    hd0 = HalfDeck(theme="Cats", variant=0, cards=[])
    assert hd0.full_name == "Cats"

    hd1 = HalfDeck(theme="Cats", variant=1, cards=[])
    assert hd1.full_name == "Cats (1)"


def test_card_to_dck_line() -> None:
    card = Card(count=4, set_code="M21", collector_number="123", name="Lightning Bolt")
    assert card.to_dck_line() == "4 [M21:123] Lightning Bolt"


# --- create_random_jumpstart_deck ---


def test_create_random_jumpstart_deck() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_jumpstart_registry(root)
        (root / "tmp").mkdir()

        # Clear cache so test uses our temp data
        with patch("magebench.game.jumpstart._cached_representatives", None):
            deck_path, display_name = create_random_jumpstart_deck(root)

        full_path = root / deck_path
        assert full_path.exists()
        content = full_path.read_text()
        assert content.startswith("NAME:")
        assert display_name  # non-empty
        # Should be a 40-card deck
        lines = [line for line in content.strip().split("\n") if not line.startswith("NAME:")]
        total_cards = sum(int(line.split()[0]) for line in lines)
        assert total_cards == 40


def test_create_random_jumpstart_deck_exclude_themes() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_jumpstart_registry(root)
        (root / "tmp").mkdir()

        # Exclude Cats — should get Dogs + Lightning
        with patch("magebench.game.jumpstart._cached_representatives", None):
            deck_path, display_name = create_random_jumpstart_deck(root, exclude_themes={"Cats"})

        full_path = root / deck_path
        content = full_path.read_text()
        assert "Dogs" in content
        assert "Lightning" in content
        assert "Cats" not in content
        assert "Dogs" in display_name and "Lightning" in display_name
