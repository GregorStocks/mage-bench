"""Tests for puppeteer.jumpstart module."""

from __future__ import annotations

import tempfile
from pathlib import Path

from puppeteer.jumpstart import (
    create_random_jumpstart_deck,
    generate_dck,
    parse_jumpstart_txt,
    pick_representatives,
)

SAMPLE_TXT = """\
# Cats
1 JMP 1 Savannah Lions
1 JMP 2 King of the Pride
1 JMP 3 Regal Caracal
1 JMP 4 Leonin Snarecaster
1 JMP 5 Felidar Cub
1 JMP 6 Basri's Acolyte
1 JMP 7 Skyhunter Patrol
1 JMP 8 Make a Stand
1 JMP 9 Impeccable Timing
1 JMP 10 Weight of Conscience
1 JMP 11 Leonin Scimitar
1 JMP 12 Ingenious Leonin
1 JMP 30 Thriving Heath
7 JMP 50 Plains

# Cats (2)
1 JMP 1 Savannah Lions
1 JMP 2 King of the Pride
1 JMP 13 Ajani's Pridemate
1 JMP 4 Leonin Snarecaster
1 JMP 14 Trained Caracal
1 JMP 6 Basri's Acolyte
1 JMP 15 Savannah Sage
1 JMP 8 Make a Stand
1 JMP 9 Impeccable Timing
1 JMP 10 Weight of Conscience
1 JMP 11 Leonin Scimitar
1 JMP 12 Ingenious Leonin
1 JMP 30 Thriving Heath
7 JMP 50 Plains

# Dogs
1 JMP 100 Alpine Watchdog
1 JMP 101 Pack Leader
1 JMP 102 Rambunctious Mutt
1 JMP 103 Selfless Savior
1 JMP 104 Bolt Hound
1 JMP 105 Release the Dogs
1 JMP 106 Trusted Watchdog
1 JMP 107 Resolute Watchdog
1 JMP 108 Ferocious Pup
1 JMP 109 Isamaru, Hound of Konda
1 JMP 110 Collar the Culprit
1 JMP 111 Angelic Gift
1 JMP 30 Thriving Heath
7 JMP 50 Plains

# Lightning
1 JMP 200 Lightning Bolt
1 JMP 201 Chain Lightning
1 JMP 202 Ball Lightning
1 JMP 203 Lightning Elemental
1 JMP 204 Goblin Electromancer
1 JMP 205 Thermo-Alchemist
1 JMP 206 Viashino Pyromancer
1 JMP 207 Shock
1 JMP 208 Searing Spear
1 JMP 209 Lightning Strike
1 JMP 210 Incinerate
1 JMP 211 Firebolt
1 JMP 35 Thriving Bluff
7 JMP 64 Mountain
"""


def test_parse_jumpstart_txt() -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(SAMPLE_TXT)
        f.flush()
        half_decks = parse_jumpstart_txt(Path(f.name))

    assert len(half_decks) == 4
    assert half_decks[0].theme == "Cats"
    assert half_decks[0].variant == 0
    assert half_decks[0].card_count == 20
    assert half_decks[1].theme == "Cats"
    assert half_decks[1].variant == 2
    assert half_decks[2].theme == "Dogs"
    assert half_decks[3].theme == "Lightning"


def test_pick_representatives() -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(SAMPLE_TXT)
        f.flush()
        half_decks = parse_jumpstart_txt(Path(f.name))

    reps = pick_representatives(half_decks)
    # Should pick variant 0 of Cats (not variant 2), plus Dogs and Lightning
    assert len(reps) == 3
    themes = {hd.theme for hd in reps}
    assert themes == {"Cats", "Dogs", "Lightning"}
    cats = next(hd for hd in reps if hd.theme == "Cats")
    assert cats.variant == 0


def test_generate_dck() -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(SAMPLE_TXT)
        f.flush()
        half_decks = parse_jumpstart_txt(Path(f.name))

    reps = pick_representatives(half_decks)
    cats = next(hd for hd in reps if hd.theme == "Cats")
    dogs = next(hd for hd in reps if hd.theme == "Dogs")

    dck = generate_dck(cats, dogs)
    assert dck.startswith("NAME:Cats + Dogs\n")
    lines = dck.strip().split("\n")
    assert len(lines) > 1
    # Should contain cards from both halves
    assert any("Savannah Lions" in line for line in lines)
    assert any("Alpine Watchdog" in line for line in lines)


def test_generate_dck_cross_color() -> None:
    """Half-decks from different colors can be combined."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(SAMPLE_TXT)
        f.flush()
        half_decks = parse_jumpstart_txt(Path(f.name))

    reps = pick_representatives(half_decks)
    cats = next(hd for hd in reps if hd.theme == "Cats")
    lightning = next(hd for hd in reps if hd.theme == "Lightning")

    dck = generate_dck(cats, lightning)
    assert "Cats + Lightning" in dck or "Lightning + Cats" in dck
    # Both Plains and Mountain should appear
    assert "Plains" in dck
    assert "Mountain" in dck


def test_create_random_jumpstart_deck() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        # Set up the expected file structure
        txt_dir = project_root / "Mage.Client" / "release" / "sample-decks" / "Jumpstart"
        txt_dir.mkdir(parents=True)
        (txt_dir / "jumpstart_custom.txt").write_text(SAMPLE_TXT)
        (project_root / "tmp").mkdir()

        deck_path = create_random_jumpstart_deck(project_root)
        full_path = project_root / deck_path
        assert full_path.exists()
        content = full_path.read_text()
        assert content.startswith("NAME:")
        # Should be a 40-card deck
        lines = [line for line in content.strip().split("\n") if not line.startswith("NAME:")]
        total_cards = 0
        for line in lines:
            count = int(line.split()[0])
            total_cards += count
        assert total_cards == 40


def test_create_random_jumpstart_deck_exclude_themes() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        txt_dir = project_root / "Mage.Client" / "release" / "sample-decks" / "Jumpstart"
        txt_dir.mkdir(parents=True)
        (txt_dir / "jumpstart_custom.txt").write_text(SAMPLE_TXT)
        (project_root / "tmp").mkdir()

        # Exclude Cats — should get Dogs + Lightning
        deck_path = create_random_jumpstart_deck(project_root, exclude_themes={"Cats"})
        full_path = project_root / deck_path
        content = full_path.read_text()
        assert "Dogs" in content
        assert "Lightning" in content
        assert "Cats" not in content
