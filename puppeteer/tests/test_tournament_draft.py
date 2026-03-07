"""Tests for tournament draft script.

Unit tests for draft order generation, pack selection, response parsing,
and golden prompt tests that verify the exact prompt format sent to LLMs.

Golden tests use real Jumpstart packs from data/decks/jumpstart/ and
oracle text cached from Scryfall in golden/draft_prompts/oracle_cache.json.

To update golden files after intentional changes:
    UPDATE_DRAFT_GOLDEN=1 make test
"""

import json
import os
from pathlib import Path

import pytest

from puppeteer.jumpstart import load_jumpstart_themes
from scripts.tournament_draft import (
    _fetch_oracle_texts,
    build_draft_system_prompt,
    build_draft_user_prompt,
    parse_pick,
    snake_draft_order,
)

_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_DIR = Path(__file__).parent / "golden" / "draft_prompts"
UPDATE_MODE = bool(os.environ.get("UPDATE_DRAFT_GOLDEN"))

# Two specific packs used for golden tests (alphabetically first and second
# among packs that won't change — these are core JMP set packs).
GOLDEN_PACK_THEMES = ["Angels", "Cats"]


# -- Fixtures --


@pytest.fixture(scope="module")
def all_packs():
    """Load all real Jumpstart half-deck packs from the repo."""
    return load_jumpstart_themes(_ROOT)


@pytest.fixture(scope="module")
def golden_packs(all_packs):
    """Return the two specific packs used for golden tests."""
    by_theme = {hd.theme: hd for hd in all_packs}
    for theme in GOLDEN_PACK_THEMES:
        assert theme in by_theme, f"Golden test pack {theme!r} not found in jumpstart themes"
    return [by_theme[t] for t in GOLDEN_PACK_THEMES]


@pytest.fixture(scope="module")
def oracle_cache(golden_packs):
    """Load or build oracle text cache for golden test packs.

    On first run (or UPDATE_DRAFT_GOLDEN=1), fetches from Scryfall and caches.
    Subsequent runs use the cached file for deterministic, offline tests.
    """
    cache_path = GOLDEN_DIR / "oracle_cache.json"

    if UPDATE_MODE or not cache_path.exists():
        # Fetch real oracle text for the golden packs
        oracle = _fetch_oracle_texts(golden_packs)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(oracle, indent=2, sort_keys=True) + "\n")
        return oracle

    return json.loads(cache_path.read_text())


# -- Snake draft order tests --


class TestSnakeDraftOrder:
    def test_size_8(self):
        order = snake_draft_order(8)
        assert order == [1, 2, 3, 4, 5, 6, 7, 8, 8, 7, 6, 5, 4, 3, 2, 1]
        assert len(order) == 16

    def test_size_16(self):
        order = snake_draft_order(16)
        assert len(order) == 32
        assert order[:16] == list(range(1, 17))
        assert order[16:] == list(range(16, 0, -1))

    def test_each_seed_appears_twice(self):
        for size in (4, 8, 16):
            order = snake_draft_order(size)
            for seed in range(1, size + 1):
                assert order.count(seed) == 2, f"Seed {seed} doesn't appear exactly twice in size {size}"


# -- Parse pick tests --


class TestParsePick:
    def test_single_digit(self):
        assert parse_pick("3", 4) == 3

    def test_with_text(self):
        assert parse_pick("I choose option 2 because it has good synergy.", 4) == 2

    def test_option_prefix(self):
        assert parse_pick("Option 1", 4) == 1

    def test_out_of_range_ignored(self):
        assert parse_pick("I'd pick 5 but I'll go with 2", 4) == 2

    def test_first_valid_wins(self):
        assert parse_pick("Between 1 and 3, I'll go with 1", 4) == 1

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Could not parse"):
            parse_pick("I can't decide, they all look good!", 4)

    def test_zero_invalid(self):
        with pytest.raises(ValueError, match="Could not parse"):
            parse_pick("0", 4)

    def test_whitespace(self):
        assert parse_pick("  2  \n", 4) == 2


# -- Prompt building tests (using real packs) --


class TestBuildDraftPrompts:
    def test_system_prompt_without_personality(self):
        prompt = build_draft_system_prompt(None)
        assert "drafting a Jumpstart deck" in prompt
        assert "tournament" in prompt

    def test_system_prompt_with_personality(self):
        prompt = build_draft_system_prompt("You are a villain who monologues about everything.")
        assert "drafting a Jumpstart deck" in prompt
        assert "villain" in prompt

    def test_user_prompt_round_1(self, all_packs, oracle_cache):
        options = all_packs[:4]
        prompt = build_draft_user_prompt(1, options, oracle_cache)
        assert "Pick 1 of 2" in prompt
        assert f"Option 1: {options[0].theme}" in prompt
        assert f"Option 4: {options[3].theme}" in prompt
        assert "1-4" in prompt
        assert "already picked" not in prompt

    def test_user_prompt_round_2(self, all_packs, oracle_cache):
        picked = all_packs[0]
        options = all_packs[1:5]
        prompt = build_draft_user_prompt(2, options, oracle_cache, already_picked=picked)
        assert "Pick 2 of 2" in prompt
        assert f"already picked: {picked.theme}" in prompt
        assert f"Option 1: {options[0].theme}" in prompt

    def test_oracle_text_included(self, golden_packs, oracle_cache):
        # Use real packs — verify oracle text appears for non-land cards
        prompt = build_draft_user_prompt(1, golden_packs, oracle_cache)
        # All non-land cards should have type lines from Scryfall
        for pack in golden_packs:
            for card in pack.cards:
                if card.name not in {"Plains", "Island", "Swamp", "Mountain", "Forest"}:
                    oracle = oracle_cache.get(card.name, {})
                    if oracle.get("type_line"):
                        assert oracle["type_line"] in prompt, f"Type line for {card.name} not in prompt"

    def test_basic_lands_simplified(self, golden_packs, oracle_cache):
        prompt = build_draft_user_prompt(1, golden_packs, oracle_cache)
        # Real packs have basic lands — they should show as "Nx Land — Basic Land"
        assert "Basic Land" in prompt


# -- Golden prompt tests (real packs, cached oracle text) --


class TestGoldenDraftPrompts:
    """Verify exact prompt format against golden reference files.

    Uses real Jumpstart packs (Angels, Cats) loaded from data/decks/jumpstart/
    and real Scryfall oracle text cached in golden/draft_prompts/oracle_cache.json.
    """

    def test_round_1_prompt(self, golden_packs, oracle_cache):
        """Golden test for round 1 draft prompt (no prior pick)."""
        golden_path = GOLDEN_DIR / "round_1_pick.json"

        system = build_draft_system_prompt("You play to win. Evaluate every option by expected win rate.")
        user = build_draft_user_prompt(1, golden_packs, oracle_cache)

        actual = {"system": system, "user": user}

        if UPDATE_MODE:
            golden_path.parent.mkdir(parents=True, exist_ok=True)
            golden_path.write_text(json.dumps(actual, indent=2) + "\n")
            return

        assert golden_path.exists(), (
            f"Golden file missing: {golden_path}\nRun UPDATE_DRAFT_GOLDEN=1 make test to generate."
        )
        expected = json.loads(golden_path.read_text())
        assert actual["system"] == expected["system"], "System prompt changed"
        assert actual["user"] == expected["user"], "User message changed"

    def test_round_2_prompt(self, golden_packs, oracle_cache):
        """Golden test for round 2 draft prompt (has prior pick)."""
        golden_path = GOLDEN_DIR / "round_2_pick.json"
        already_picked = golden_packs[0]  # Angels

        system = build_draft_system_prompt("You play to win. Evaluate every option by expected win rate.")
        user = build_draft_user_prompt(2, [golden_packs[1]], oracle_cache, already_picked=already_picked)

        actual = {"system": system, "user": user}

        if UPDATE_MODE:
            golden_path.parent.mkdir(parents=True, exist_ok=True)
            golden_path.write_text(json.dumps(actual, indent=2) + "\n")
            return

        assert golden_path.exists(), (
            f"Golden file missing: {golden_path}\nRun UPDATE_DRAFT_GOLDEN=1 make test to generate."
        )
        expected = json.loads(golden_path.read_text())
        assert actual["system"] == expected["system"], "System prompt changed"
        assert actual["user"] == expected["user"], "User message changed"
