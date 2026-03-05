"""Tests for tournament draft script.

Unit tests for draft order generation, pack selection, response parsing,
and golden prompt tests that verify the exact prompt format sent to LLMs.

To update golden files after intentional changes:
    UPDATE_DRAFT_GOLDEN=1 make test
"""

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "puppeteer" / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

from puppeteer.jumpstart import Card, HalfDeck  # noqa: E402
from scripts.tournament_draft import (  # noqa: E402
    build_draft_system_prompt,
    build_draft_user_prompt,
    parse_pick,
    snake_draft_order,
)

GOLDEN_DIR = Path(__file__).parent / "golden" / "draft_prompts"
UPDATE_MODE = bool(os.environ.get("UPDATE_DRAFT_GOLDEN"))


# -- Test fixtures --


def _make_half_deck(theme: str, cards: list[tuple[int, str]] | None = None) -> HalfDeck:
    """Create a HalfDeck for testing."""
    if cards is None:
        cards = [
            (1, "Card A"),
            (1, "Card B"),
            (1, "Card C"),
            (8, "Mountain"),
        ]
    return HalfDeck(
        theme=theme,
        variant=0,
        cards=[Card(count=count, set_code="TST", collector_number="1", name=name) for count, name in cards],
    )


SAMPLE_ORACLE: dict[str, dict] = {
    "Dragonloft Idol": {
        "mana_cost": "{2}",
        "type_line": "Artifact Creature — Cleric",
        "oracle_text": "As long as you control a Dragon, Dragonloft Idol gets +1/+1 and has flying.",
        "power": "2",
        "toughness": "2",
    },
    "Dragonspeaker Shaman": {
        "mana_cost": "{1}{R}{R}",
        "type_line": "Creature — Human Barbarian Shaman",
        "oracle_text": "Dragon spells you cast cost {2} less to cast.",
        "power": "2",
        "toughness": "2",
    },
    "Feline Sovereign": {
        "mana_cost": "{2}{G}",
        "type_line": "Creature — Cat",
        "oracle_text": (
            "Other Cats you control get +1/+1 and have protection from Dogs.\n"
            "Whenever one or more Cats you control deal combat damage to a player, "
            "destroy up to one target artifact or enchantment that player controls."
        ),
        "power": "3",
        "toughness": "3",
    },
    "Card A": {
        "mana_cost": "{1}{R}",
        "type_line": "Creature — Goblin",
        "oracle_text": "Haste",
        "power": "2",
        "toughness": "1",
    },
    "Card B": {
        "mana_cost": "{2}{R}",
        "type_line": "Sorcery",
        "oracle_text": "Deal 3 damage to any target.",
    },
    "Card C": {
        "mana_cost": "{3}{R}{R}",
        "type_line": "Creature — Dragon",
        "oracle_text": "Flying",
        "power": "4",
        "toughness": "4",
    },
}


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
        # "5" is out of range for 4 options, "2" should be picked
        assert parse_pick("I'd pick 5 but I'll go with 2", 4) == 2

    def test_first_valid_wins(self):
        # Multiple valid numbers — first one wins
        assert parse_pick("Between 1 and 3, I'll go with 1", 4) == 1

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Could not parse"):
            parse_pick("I can't decide, they all look good!", 4)

    def test_zero_invalid(self):
        with pytest.raises(ValueError, match="Could not parse"):
            parse_pick("0", 4)

    def test_whitespace(self):
        assert parse_pick("  2  \n", 4) == 2


# -- Prompt building tests --


class TestBuildDraftPrompts:
    def test_system_prompt_without_personality(self):
        prompt = build_draft_system_prompt(None)
        assert "drafting a Jumpstart deck" in prompt
        assert "tournament" in prompt

    def test_system_prompt_with_personality(self):
        prompt = build_draft_system_prompt("You are a villain who monologues about everything.")
        assert "drafting a Jumpstart deck" in prompt
        assert "villain" in prompt

    def test_user_prompt_round_1(self):
        options = [
            _make_half_deck("Dragons"),
            _make_half_deck("Cats"),
            _make_half_deck("Elves"),
            _make_half_deck("Angels"),
        ]
        prompt = build_draft_user_prompt(1, options, SAMPLE_ORACLE)
        assert "Pick 1 of 2" in prompt
        assert "Option 1: Dragons" in prompt
        assert "Option 4: Angels" in prompt
        assert "1-4" in prompt
        # Should not mention previous picks
        assert "already picked" not in prompt

    def test_user_prompt_round_2(self):
        picked = _make_half_deck("Dragons")
        options = [
            _make_half_deck("Cats"),
            _make_half_deck("Elves"),
            _make_half_deck("Angels"),
            _make_half_deck("Goblins"),
        ]
        prompt = build_draft_user_prompt(2, options, SAMPLE_ORACLE, already_picked=picked)
        assert "Pick 2 of 2" in prompt
        assert "already picked: Dragons" in prompt
        assert "Option 1: Cats" in prompt

    def test_oracle_text_included(self):
        options = [_make_half_deck("Test", cards=[(1, "Card A"), (8, "Mountain")])]
        prompt = build_draft_user_prompt(1, options, SAMPLE_ORACLE)
        assert "{1}{R}" in prompt  # mana cost
        assert "Creature — Goblin" in prompt  # type line
        assert "Haste" in prompt  # oracle text
        assert "2/1" in prompt  # P/T

    def test_basic_lands_simplified(self):
        options = [_make_half_deck("Test", cards=[(1, "Card A"), (8, "Mountain")])]
        prompt = build_draft_user_prompt(1, options, SAMPLE_ORACLE)
        assert "8x Mountain — Basic Land" in prompt


# -- Golden prompt tests --


def _make_golden_packs() -> list[HalfDeck]:
    """Create deterministic packs for golden testing."""
    dragons = HalfDeck(
        theme="Dragons",
        variant=0,
        cards=[
            Card(count=1, set_code="JMP", collector_number="463", name="Dragonloft Idol"),
            Card(count=1, set_code="JMP", collector_number="312", name="Dragonspeaker Shaman"),
            Card(count=8, set_code="JMP", collector_number="64", name="Mountain"),
        ],
    )
    cats = HalfDeck(
        theme="Cats",
        variant=0,
        cards=[
            Card(count=1, set_code="M21", collector_number="374", name="Feline Sovereign"),
            Card(count=8, set_code="JMP", collector_number="74", name="Forest"),
        ],
    )
    return [dragons, cats]


class TestGoldenDraftPrompts:
    """Verify exact prompt format against golden reference files."""

    def test_round_1_prompt(self):
        """Golden test for round 1 draft prompt (no prior pick)."""
        golden_path = GOLDEN_DIR / "round_1_pick.json"
        packs = _make_golden_packs()

        system = build_draft_system_prompt("You play to win. Evaluate every option by expected win rate.")
        user = build_draft_user_prompt(1, packs, SAMPLE_ORACLE)

        actual = {
            "system": system,
            "user": user,
        }

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

    def test_round_2_prompt(self):
        """Golden test for round 2 draft prompt (has prior pick)."""
        golden_path = GOLDEN_DIR / "round_2_pick.json"
        packs = _make_golden_packs()
        already_picked = packs[0]  # Dragons

        system = build_draft_system_prompt("You play to win. Evaluate every option by expected win rate.")
        user = build_draft_user_prompt(2, [packs[1]], SAMPLE_ORACLE, already_picked=already_picked)

        actual = {
            "system": system,
            "user": user,
        }

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
