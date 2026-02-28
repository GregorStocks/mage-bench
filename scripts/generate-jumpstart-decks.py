#!/usr/bin/env python3
"""Generate Jumpstart 40-card .dck files from half-deck pairs.

Parses jumpstart_custom.txt, groups half-decks by color (detected from basic
lands), and generates one .dck file for each pair of distinct themes within
the same color. Uses variant 1 of each theme.

Usage:
    uv run python scripts/generate-jumpstart-decks.py
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

JUMPSTART_TXT = Path("Mage.Client/release/sample-decks/Jumpstart/jumpstart_custom.txt")
OUTPUT_DIR = Path("Mage.Client/release/sample-decks/Jumpstart")

# Basic land name -> color letter
_LAND_TO_COLOR = {
    "Plains": "W",
    "Island": "U",
    "Swamp": "B",
    "Mountain": "R",
    "Forest": "G",
}

# Color letter -> sort order (WUBRG)
_COLOR_ORDER = {"W": 0, "U": 1, "B": 2, "R": 3, "G": 4}


@dataclass
class Card:
    count: int
    set_code: str
    collector_number: str
    name: str

    def to_dck_line(self) -> str:
        return f"{self.count} [{self.set_code}:{self.collector_number}] {self.name}"


@dataclass
class HalfDeck:
    theme: str  # base theme name, e.g. "Cats"
    variant: int  # 1, 2, 3, ...
    cards: list[Card] = field(default_factory=list)
    color: str = ""  # W, U, B, R, G

    @property
    def card_count(self) -> int:
        return sum(c.count for c in self.cards)

    @property
    def full_name(self) -> str:
        if self.variant > 0:
            return f"{self.theme} ({self.variant})"
        return self.theme


def parse_card_line(line: str) -> Card:
    """Parse a jumpstart.txt card line into a Card.

    Input format: '1 JMP 407 Keeper of Fables'
    """
    m = re.match(r"^(\d+)\s+(\S+)\s+(\S+)\s+(.+)$", line)
    assert m, f"Could not parse card line: {line!r}"
    return Card(
        count=int(m.group(1)),
        set_code=m.group(2),
        collector_number=m.group(3),
        name=m.group(4),
    )


def parse_jumpstart_txt(path: Path) -> list[HalfDeck]:
    """Parse jumpstart_custom.txt into a list of HalfDecks."""
    text = path.read_text()
    half_decks: list[HalfDeck] = []
    current: HalfDeck | None = None

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            # Save previous half-deck
            if current is not None:
                half_decks.append(current)
            # Parse theme header: "# Cats (2)" or "# Basri" or "# Discarding 1"
            header = line[2:].strip()
            m = re.match(r"^(.+?)\s*\((\d+)\)$", header)
            if m:
                theme, variant = m.group(1), int(m.group(2))
            else:
                theme, variant = header, 0
            current = HalfDeck(theme=theme, variant=variant)
        elif line and current is not None:
            current.cards.append(parse_card_line(line))
        elif not line and current is not None and current.cards:
            half_decks.append(current)
            current = None

    # Don't forget the last one
    if current is not None and current.cards:
        half_decks.append(current)

    return half_decks


def detect_color(half_deck: HalfDeck) -> str:
    """Detect the color of a half-deck from its basic lands."""
    colors: set[str] = set()
    for card in half_deck.cards:
        for land_name, color in _LAND_TO_COLOR.items():
            if land_name in card.name:
                colors.add(color)
    assert colors, f"No basic lands found in {half_deck.full_name}"
    return "".join(sorted(colors, key=lambda c: _COLOR_ORDER[c]))


def assign_colors(half_decks: list[HalfDeck]) -> None:
    """Detect and assign color to each half-deck. Mutates in place."""
    for hd in half_decks:
        hd.color = detect_color(hd)


def pick_representative(half_decks: list[HalfDeck]) -> dict[str, dict[str, HalfDeck]]:
    """Group half-decks by color, pick variant 1 (or lowest) per theme.

    Returns {color: {theme: half_deck}}.
    """
    by_color: dict[str, dict[str, list[HalfDeck]]] = {}
    for hd in half_decks:
        if len(hd.color) != 1:
            # Skip multi-color (Rainbow)
            continue
        by_color.setdefault(hd.color, {}).setdefault(hd.theme, []).append(hd)

    result: dict[str, dict[str, HalfDeck]] = {}
    for color, themes in by_color.items():
        result[color] = {}
        for theme, variants in themes.items():
            # Pick variant 1, or the lowest variant number
            variants.sort(key=lambda h: h.variant)
            result[color][theme] = variants[0]

    return result


def generate_dck(half1: HalfDeck, half2: HalfDeck) -> str:
    """Generate .dck file content from two half-decks."""
    combined_count = half1.card_count + half2.card_count
    assert combined_count == 40, (
        f"Combined deck {half1.theme} + {half2.theme} has {combined_count} cards, expected 40"
    )

    lines = [f"NAME:{half1.theme} + {half2.theme}"]
    for card in half1.cards:
        lines.append(card.to_dck_line())
    for card in half2.cards:
        lines.append(card.to_dck_line())
    return "\n".join(lines) + "\n"


def deck_filename(color: str, theme1: str, theme2: str) -> str:
    """Generate a .dck filename from color and theme names."""
    # Sort themes alphabetically for deterministic naming
    t1, t2 = sorted([theme1, theme2])
    # Sanitize theme names for filenames
    t1_safe = t1.replace(" ", "-")
    t2_safe = t2.replace(" ", "-")
    return f"{color}-{t1_safe}-{t2_safe}.dck"


def main() -> None:
    assert JUMPSTART_TXT.exists(), f"Source file not found: {JUMPSTART_TXT}"

    half_decks = parse_jumpstart_txt(JUMPSTART_TXT)
    print(f"Parsed {len(half_decks)} half-decks")

    assign_colors(half_decks)

    # Validate card counts
    for hd in half_decks:
        assert hd.card_count == 20, (
            f"{hd.full_name} has {hd.card_count} cards, expected 20"
        )

    representatives = pick_representative(half_decks)

    total = 0
    for color in sorted(representatives.keys(), key=lambda c: _COLOR_ORDER[c]):
        themes = representatives[color]
        theme_names = sorted(themes.keys())
        count = 0
        for t1, t2 in combinations(theme_names, 2):
            content = generate_dck(themes[t1], themes[t2])
            filename = deck_filename(color, t1, t2)
            output_path = OUTPUT_DIR / filename
            output_path.write_text(content)
            count += 1
        print(
            f"  {color}: {count} decks from {len(theme_names)} themes ({', '.join(theme_names)})"
        )
        total += count

    print(f"Generated {total} Jumpstart decks in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
