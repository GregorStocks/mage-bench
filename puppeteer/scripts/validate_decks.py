#!/usr/bin/env python3
"""Validate .dck deck files against the XMage card database.

Parses set definition Java files to build a set of valid (setCode, cardNumber)
pairs, then checks all .dck files for references to cards that don't exist
in the database.

Usage:
    uv run python puppeteer/scripts/validate_decks.py [deck_dir ...]

If no deck directories are given, checks all .dck files under
Mage.Client/release/sample-decks/.
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

SETS_DIR = PROJECT_ROOT / "Mage.Sets" / "src" / "mage" / "sets"
DEFAULT_DECK_DIR = PROJECT_ROOT / "Mage.Client" / "release" / "sample-decks"

# Matches: cards.add(new SetCardInfo("Card Name", 123, ...))
# Handles escaped quotes in card names like "Kongming, \"Sleeping Dragon\""
_SET_CARD_RE = re.compile(
    r'cards\.add\(new SetCardInfo\("((?:[^"\\]|\\.)+)",\s*"?(\d+)"?\s*,'
)

# Matches deck lines: COUNT [SET:NUM] Card Name
# Also handles SB: prefix
_DECK_LINE_RE = re.compile(
    r"^(?:SB:\s*)?(\d+)\s+\[([A-Z0-9]+):(\d+)\]\s+(.+)$"
)

# Matches the set code from the constructor: super("Name", "CODE", ...)
_SET_CODE_RE = re.compile(r'super\("[^"]*",\s*"([^"]+)"')


def parse_set_file(path: Path) -> set[tuple[str, str]]:
    """Parse a set definition .java file, returning valid (setCode, cardNumber) pairs."""
    text = path.read_text()

    # Extract set code
    m = _SET_CODE_RE.search(text)
    if not m:
        return set()
    set_code = m.group(1)

    # Extract all card numbers
    entries = set()
    for m in _SET_CARD_RE.finditer(text):
        card_number = m.group(2)
        entries.add((set_code, card_number))
    return entries


def build_card_database(sets_dir: Path) -> set[tuple[str, str]]:
    """Build set of all valid (setCode, cardNumber) pairs from set definitions."""
    db: set[tuple[str, str]] = set()
    for java_file in sorted(sets_dir.glob("*.java")):
        db |= parse_set_file(java_file)
    return db


def validate_deck(
    deck_path: Path, card_db: set[tuple[str, str]]
) -> list[tuple[str, str, str, int]]:
    """Validate a deck file. Returns list of (setCode, cardNumber, cardName, line_num) for missing cards."""
    missing = []
    for line_num, line in enumerate(deck_path.read_text().splitlines(), 1):
        m = _DECK_LINE_RE.match(line.strip())
        if not m:
            continue
        _count, set_code, card_number, card_name = (
            m.group(1),
            m.group(2),
            m.group(3),
            m.group(4).strip(),
        )
        if (set_code, card_number) not in card_db:
            missing.append((set_code, card_number, card_name, line_num))
    return missing


def main() -> None:
    args = sys.argv[1:]
    if args:
        deck_dirs = [Path(a) for a in args]
    else:
        deck_dirs = [DEFAULT_DECK_DIR]

    print(f"Building card database from {SETS_DIR}...")
    card_db = build_card_database(SETS_DIR)
    print(f"  {len(card_db)} valid (set, number) entries across all sets")
    print()

    total_decks = 0
    total_bad_decks = 0
    total_missing = 0

    for deck_dir in deck_dirs:
        deck_files = sorted(deck_dir.rglob("*.dck"))
        if not deck_files:
            print(f"No .dck files found in {deck_dir}")
            continue

        for deck_path in deck_files:
            total_decks += 1
            missing = validate_deck(deck_path, card_db)
            if missing:
                total_bad_decks += 1
                total_missing += len(missing)
                rel = deck_path.relative_to(PROJECT_ROOT)
                print(f"{rel}:")
                for set_code, card_num, card_name, line_num in missing:
                    print(f"  L{line_num}: {card_name} [{set_code}:{card_num}] - NOT IN DATABASE")

    print()
    print(f"Checked {total_decks} decks: {total_bad_decks} with missing cards, {total_missing} missing total")
    sys.exit(1 if total_missing > 0 else 0)


if __name__ == "__main__":
    main()
