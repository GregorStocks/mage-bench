"""Jumpstart half-deck parsing and runtime deck generation.

Parses jumpstart_custom.txt into half-decks, picks one representative variant
per theme, and combines random pairs into 40-card .dck files at game creation
time. Any two half-decks can be paired regardless of color.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from pathlib import Path

JUMPSTART_TXT = Path("Mage.Client/release/sample-decks/Jumpstart/jumpstart_custom.txt")


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

    @property
    def card_count(self) -> int:
        return sum(c.count for c in self.cards)

    @property
    def full_name(self) -> str:
        if self.variant > 0:
            return f"{self.theme} ({self.variant})"
        return self.theme


def parse_jumpstart_txt(path: Path) -> list[HalfDeck]:
    """Parse jumpstart_custom.txt into a list of HalfDecks."""
    text = path.read_text()
    half_decks: list[HalfDeck] = []
    current: HalfDeck | None = None

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            if current is not None:
                half_decks.append(current)
            header = line[2:].strip()
            m = re.match(r"^(.+?)\s*\((\d+)\)$", header)
            if m:
                theme, variant = m.group(1), int(m.group(2))
            else:
                theme, variant = header, 0
            current = HalfDeck(theme=theme, variant=variant)
        elif line and current is not None:
            m = re.match(r"^(\d+)\s+(\S+)\s+(\S+)\s+(.+)$", line)
            assert m, f"Could not parse card line: {line!r}"
            current.cards.append(
                Card(
                    count=int(m.group(1)),
                    set_code=m.group(2),
                    collector_number=m.group(3),
                    name=m.group(4),
                )
            )
        elif not line and current is not None and current.cards:
            half_decks.append(current)
            current = None

    if current is not None and current.cards:
        half_decks.append(current)

    return half_decks


def pick_representatives(half_decks: list[HalfDeck]) -> list[HalfDeck]:
    """Pick one representative variant per theme (variant 1, or lowest).

    Returns a flat list of representative half-decks.
    """
    by_theme: dict[str, list[HalfDeck]] = {}
    for hd in half_decks:
        by_theme.setdefault(hd.theme, []).append(hd)

    result: list[HalfDeck] = []
    for theme in sorted(by_theme.keys()):
        variants = by_theme[theme]
        variants.sort(key=lambda h: h.variant)
        result.append(variants[0])

    return result


def generate_dck(half1: HalfDeck, half2: HalfDeck) -> str:
    """Generate .dck file content from two half-decks."""
    combined_count = half1.card_count + half2.card_count
    assert combined_count == 40, f"Combined deck {half1.theme} + {half2.theme} has {combined_count} cards, expected 40"

    lines = [f"NAME:{half1.theme} + {half2.theme}"]
    for card in half1.cards:
        lines.append(card.to_dck_line())
    for card in half2.cards:
        lines.append(card.to_dck_line())
    return "\n".join(lines) + "\n"


# Cached parsed half-decks (parsed once per process)
_cached_representatives: list[HalfDeck] | None = None


def _get_representatives(project_root: Path) -> list[HalfDeck]:
    """Get cached representative half-decks, parsing on first call."""
    global _cached_representatives
    if _cached_representatives is None:
        txt_path = project_root / JUMPSTART_TXT
        assert txt_path.exists(), f"Jumpstart data not found: {txt_path}"
        half_decks = parse_jumpstart_txt(txt_path)
        for hd in half_decks:
            assert hd.card_count == 20, f"{hd.full_name} has {hd.card_count} cards, expected 20"
        _cached_representatives = pick_representatives(half_decks)
    return _cached_representatives


def create_random_jumpstart_deck(project_root: Path, exclude_themes: set[str] | None = None) -> Path:
    """Create a random 40-card Jumpstart deck by combining two random half-decks.

    Writes the .dck to tmp/ and returns the path relative to project_root.
    """
    reps = _get_representatives(project_root)

    available = reps
    if exclude_themes:
        available = [hd for hd in reps if hd.theme not in exclude_themes]
    assert len(available) >= 2, f"Not enough half-decks available ({len(available)}), need at least 2"

    half1, half2 = random.sample(available, 2)

    content = generate_dck(half1, half2)

    # Write to tmp/ directory
    tmp_dir = project_root / "tmp" / "jumpstart-decks"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    t1, t2 = sorted([half1.theme, half2.theme])
    safe_name = f"{t1.replace(' ', '-')}+{t2.replace(' ', '-')}.dck"
    deck_path = tmp_dir / safe_name
    deck_path.write_text(content)

    return deck_path.relative_to(project_root)
