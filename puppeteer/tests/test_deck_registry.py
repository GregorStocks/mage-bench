"""Tests for deck registry completeness and validity."""

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "data" / "decks"

# Regex for .dck card lines: "1 [SET:NUM] Card Name" or "SB: 1 [SET:NUM] Card Name"
_CARD_LINE_RE = re.compile(r"^(?:SB:\s*)?(\d+)\s+\[\S+:\S+\]\s+.+$")

FORMAT_DIRS = ["standard", "modern", "legacy", "commander"]


@pytest.fixture(scope="module")
def all_deck_files() -> list[Path]:
    """Collect all deck JSON files across all formats."""
    files = []
    for fmt in FORMAT_DIRS:
        fmt_dir = DATA_DIR / fmt
        if fmt_dir.is_dir():
            files.extend(sorted(fmt_dir.glob("*.json")))
    return files


@pytest.fixture(scope="module")
def jumpstart_files() -> list[Path]:
    """Collect all Jumpstart theme JSON files."""
    jmp_dir = DATA_DIR / "jumpstart"
    if jmp_dir.is_dir():
        return sorted(jmp_dir.glob("*.json"))
    return []


def test_deck_files_exist(all_deck_files):
    """At least one deck file exists per format."""
    for fmt in FORMAT_DIRS:
        fmt_files = [f for f in all_deck_files if f.parent.name == fmt]
        assert fmt_files, f"No deck files found in data/decks/{fmt}/"


def test_deck_files_valid_json(all_deck_files):
    """Every deck file is valid JSON with required fields."""
    for f in all_deck_files:
        data = json.loads(f.read_text())
        assert "name" in data, f"{f.name} missing 'name'"
        assert "cards" in data, f"{f.name} missing 'cards'"
        assert isinstance(data["name"], str) and data["name"], f"{f.name} has empty name"
        assert isinstance(data["cards"], list) and data["cards"], f"{f.name} has empty cards"
        assert "strategy" in data, f"{f.name} missing 'strategy'"


def test_card_lines_parse(all_deck_files):
    """Every card line in every deck matches .dck format."""
    for f in all_deck_files:
        data = json.loads(f.read_text())
        for i, line in enumerate(data["cards"]):
            assert _CARD_LINE_RE.match(line), f"{f.name} card line {i} doesn't match .dck format: {line!r}"


def test_no_duplicate_names(all_deck_files):
    """No two decks in the same format have the same name."""
    by_format: dict[str, list[str]] = {}
    for f in all_deck_files:
        data = json.loads(f.read_text())
        fmt = f.parent.name
        by_format.setdefault(fmt, []).append(data["name"])
    for fmt, names in by_format.items():
        dupes = [n for n in names if names.count(n) > 1]
        assert not dupes, f"Duplicate deck names in {fmt}: {set(dupes)}"


def test_jumpstart_files_valid(jumpstart_files):
    """Every Jumpstart theme file is valid with variants."""
    assert jumpstart_files, "No Jumpstart theme files found"
    for f in jumpstart_files:
        data = json.loads(f.read_text())
        assert "name" in data, f"{f.name} missing 'name'"
        assert "variants" in data, f"{f.name} missing 'variants'"
        assert isinstance(data["variants"], list) and data["variants"], f"{f.name} has empty variants"
        for vi, variant in enumerate(data["variants"]):
            assert "cards" in variant, f"{f.name} variant {vi} missing 'cards'"
            # Each half-deck should have 20 cards worth
            total = 0
            for line in variant["cards"]:
                m = _CARD_LINE_RE.match(line)
                assert m, f"{f.name} variant {vi} bad card line: {line!r}"
                total += int(m.group(1))
            assert total == 20, f"{f.name} variant {vi} has {total} cards, expected 20"


def test_jumpstart_no_duplicate_themes(jumpstart_files):
    """No two Jumpstart files have the same theme name."""
    names = []
    for f in jumpstart_files:
        data = json.loads(f.read_text())
        names.append(data["name"])
    dupes = [n for n in names if names.count(n) > 1]
    assert not dupes, f"Duplicate Jumpstart themes: {set(dupes)}"
