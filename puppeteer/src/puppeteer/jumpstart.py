"""Jumpstart half-deck parsing and runtime deck generation.

Reads half-deck themes from data/decks/jumpstart/*.json, picks one representative
variant per theme, and combines random pairs into 40-card .dck files at game
creation time. Any two half-decks can be paired regardless of color.
"""

import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path


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
    variant: int  # 0-based index within variants array
    cards: list[Card] = field(default_factory=list)

    @property
    def card_count(self) -> int:
        return sum(c.count for c in self.cards)

    @property
    def full_name(self) -> str:
        if self.variant > 0:
            return f"{self.theme} ({self.variant})"
        return self.theme


# Regex for .dck-format card lines: "1 [SET:NUM] Card Name"
_DCK_CARD_RE = re.compile(r"^(\d+)\s+\[(\S+?):(\S+?)\]\s+(.+)$")


def _parse_dck_card(line: str) -> Card:
    """Parse a .dck-format card line into a Card object."""
    m = _DCK_CARD_RE.match(line.strip())
    assert m, f"Could not parse card line: {line!r}"
    return Card(
        count=int(m.group(1)),
        set_code=m.group(2),
        collector_number=m.group(3),
        name=m.group(4).strip(),
    )


def load_jumpstart_themes(project_root: Path) -> list[HalfDeck]:
    """Load all Jumpstart themes from data/decks/jumpstart/*.json.

    Each JSON file contains one theme with a variants array. Returns one
    representative HalfDeck per theme (the first variant).
    """
    registry_dir = project_root / "data" / "decks" / "jumpstart"
    assert registry_dir.is_dir(), f"Jumpstart registry not found: {registry_dir}"

    half_decks: list[HalfDeck] = []
    for json_file in sorted(registry_dir.glob("*.json")):
        data = json.loads(json_file.read_text())
        theme = data["name"]
        variants = data.get("variants", [])
        assert variants, f"Jumpstart theme {theme!r} has no variants in {json_file}"
        # Use first variant as representative
        cards = [_parse_dck_card(line) for line in variants[0]["cards"]]
        half_decks.append(HalfDeck(theme=theme, variant=0, cards=cards))

    assert half_decks, f"No Jumpstart themes found in {registry_dir}"
    return half_decks


def generate_dck(half1: HalfDeck, half2: HalfDeck) -> str:
    """Generate .dck file content from two half-decks."""
    combined_count = half1.card_count + half2.card_count
    assert combined_count == 40, f"Combined deck {half1.theme} + {half2.theme} has {combined_count} cards, expected 40"

    lines = [f"NAME:{half1.theme} + {half2.theme}"]
    lines.extend(card.to_dck_line() for card in half1.cards)
    lines.extend(card.to_dck_line() for card in half2.cards)
    return "\n".join(lines) + "\n"


# Cached parsed half-decks (parsed once per process)
_cached_representatives: list[HalfDeck] | None = None


def _get_representatives(project_root: Path) -> list[HalfDeck]:
    """Get cached representative half-decks, loading on first call."""
    global _cached_representatives
    if _cached_representatives is None:
        _cached_representatives = load_jumpstart_themes(project_root)
        for hd in _cached_representatives:
            assert hd.card_count == 20, f"{hd.full_name} has {hd.card_count} cards, expected 20"
    return _cached_representatives


def create_random_jumpstart_deck(project_root: Path, exclude_themes: set[str] | None = None) -> tuple[Path, str]:
    """Create a random 40-card Jumpstart deck by combining two random half-decks.

    Writes the .dck to tmp/ and returns (path_relative_to_project_root, display_name).
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

    display_name = f"{t1} + {t2}"
    return deck_path.relative_to(project_root), display_name
