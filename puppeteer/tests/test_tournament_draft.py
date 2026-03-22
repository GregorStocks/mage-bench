"""Tests for tournament draft script.

Unit tests for draft order generation, pack selection, response parsing,
and golden prompt tests that verify the exact prompt format sent to LLMs.

Golden tests use real Jumpstart packs from data/decks/jumpstart/ and
oracle text cached from Scryfall in golden/draft_prompts/oracle_cache.json.

To update golden files after intentional changes:
    UPDATE_DRAFT_GOLDEN=1 make test
"""

import os
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from magebench.common.json5_utils import loads_json5
from magebench.common.json5_writer import dumps_json5
from puppeteer.jumpstart import load_jumpstart_themes
from scripts.tournament_draft import (
    _fetch_oracle_texts,
    _llm_pick,
    build_draft_system_prompt,
    build_draft_user_prompt,
    draft_order,
    parse_pick,
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
    cache_path = GOLDEN_DIR / "oracle_cache.json5"

    if UPDATE_MODE or not cache_path.exists():
        # Fetch real oracle text for the golden packs
        oracle = _fetch_oracle_texts(golden_packs)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(dumps_json5(oracle, sort_keys=True) + "\n")
        return oracle

    return loads_json5(cache_path.read_text())


def _minimal_oracle_for_packs(packs) -> dict[str, dict]:
    """Build the minimal oracle mapping required for prompt rendering tests."""
    oracle: dict[str, dict] = {}
    for pack in packs:
        for card in pack.cards:
            oracle.setdefault(card.name, {})
    return oracle


# -- Straight draft order tests --


class TestDraftOrder:
    def test_size_8(self):
        order = draft_order(8)
        assert order == [1, 2, 3, 4, 5, 6, 7, 8, 1, 2, 3, 4, 5, 6, 7, 8]
        assert len(order) == 16

    def test_size_16(self):
        order = draft_order(16)
        assert len(order) == 32
        assert order[:16] == list(range(1, 17))
        assert order[16:] == list(range(1, 17))

    def test_each_seed_appears_twice(self):
        for size in (4, 8, 16):
            order = draft_order(size)
            for seed in range(1, size + 1):
                assert order.count(seed) == 2, f"Seed {seed} doesn't appear exactly twice in size {size}"

    def test_higher_seed_always_picks_first(self):
        order = draft_order(8)
        # In each round, seed 1 picks before seed 2, etc.
        for round_start in (0, 8):
            round_order = order[round_start : round_start + 8]
            assert round_order == list(range(1, 9))


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

    def test_bold_number_at_start(self):
        assert parse_pick("**17**\n\nElves is the strongest pack here.", 64) == 17

    def test_bold_number_beats_option_in_reasoning(self):
        # Model says **17** as its pick, then references "Option 30" in analysis
        assert (
            parse_pick(
                "**17**\n\nElves is great and pairs with J22 Elves (Option 30)",
                64,
            )
            == 17
        )

    def test_leading_number_with_period(self):
        assert parse_pick("17. Elves is the best choice.", 64) == 17


def _mock_draft_response(
    content: str | None,
    *,
    thinking: str | None = None,
    prompt_tokens: int = 100,
    completion_tokens: int = 10,
) -> MagicMock:
    """Create a mock draft completion response."""
    response = MagicMock()
    response.choices = [SimpleNamespace(message=SimpleNamespace(content=content, reasoning_content=thinking))]
    response.usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    response.model_dump.return_value = {
        "choices": [
            {
                "message": {
                    "content": content,
                    "reasoning_content": thinking,
                }
            }
        ]
    }
    return response


class TestLlmPick:
    @pytest.mark.asyncio
    async def test_retries_until_response_is_bare_number(self):
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            side_effect=[
                _mock_draft_response(
                    "**17**\n\nElves is the clear pick here because it stays open.",
                    thinking="Initial hidden reasoning",
                    prompt_tokens=120,
                    completion_tokens=15,
                ),
                _mock_draft_response(
                    "17",
                    thinking="Retry hidden reasoning that should not replace the first",
                    prompt_tokens=80,
                    completion_tokens=5,
                ),
            ]
        )

        pick, reasoning, attempts, usage = await _llm_pick(
            client=client,
            model="test-model",
            system_prompt="system",
            user_prompt="user",
            reasoning_effort=None,
            num_options=64,
            log_file=StringIO(),
            pick_meta={"seed": 1, "round": 1},
        )

        assert pick == 17
        assert reasoning == "17"
        assert attempts == [
            {
                "attempt": 1,
                "response": "**17**\n\nElves is the clear pick here because it stays open.",
                "thinking": "Initial hidden reasoning",
                "accepted": False,
                "rejection_reason": "I parsed your pick, but your response included extra text.",
            },
            {
                "attempt": 2,
                "response": "17",
                "thinking": "Retry hidden reasoning that should not replace the first",
                "accepted": True,
            },
        ]
        assert usage == {"prompt_tokens": 200, "completion_tokens": 20}

        assert client.chat.completions.create.await_count == 2
        retry_messages = client.chat.completions.create.await_args_list[1].kwargs["messages"]
        assert retry_messages[-2] == {
            "role": "assistant",
            "content": "**17**\n\nElves is the clear pick here because it stays open.",
        }
        assert retry_messages[-1] == {
            "role": "user",
            "content": (
                "I parsed your pick, but your response included extra text. "
                "Reply with ONLY a single number from 1 to 64. No other text."
            ),
        }


# -- Prompt building tests (using real packs) --


class TestBuildDraftPrompts:
    def test_system_prompt_without_personality(self):
        prompt = build_draft_system_prompt(None, seed=3, num_entrants=8)
        assert "drafting a Jumpstart deck" in prompt
        assert "tournament" in prompt
        assert "straight (linear) draft with 8 players" in prompt
        assert "seed #3" in prompt

    def test_system_prompt_with_personality(self):
        prompt = build_draft_system_prompt(
            "You are a villain who monologues about everything.",
            seed=1,
            num_entrants=16,
        )
        assert "drafting a Jumpstart deck" in prompt
        assert "villain" in prompt
        assert "16 players" in prompt
        assert "seed #1" in prompt

    def test_user_prompt_round_1(self, all_packs):
        options = all_packs[:4]
        prompt = build_draft_user_prompt(1, options, _minimal_oracle_for_packs(options))
        assert "Pick 1 of 2" in prompt
        assert f"Option 1: {options[0].theme}" in prompt
        assert f"Option 4: {options[3].theme}" in prompt
        assert "1-4" in prompt
        assert "already picked" not in prompt

    def test_user_prompt_round_2(self, all_packs):
        picked = all_packs[0]
        options = all_packs[1:5]
        prompt = build_draft_user_prompt(2, options, _minimal_oracle_for_packs(options), already_picked=picked)
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
        golden_path = GOLDEN_DIR / "round_1_pick.json5"

        system = build_draft_system_prompt(
            "You play to win. Evaluate every option by expected win rate.",
            seed=3,
            num_entrants=8,
        )
        user = build_draft_user_prompt(1, golden_packs, oracle_cache)

        actual = {"system": system, "user": user}

        if UPDATE_MODE:
            golden_path.parent.mkdir(parents=True, exist_ok=True)
            golden_path.write_text(dumps_json5(actual) + "\n")
            return

        assert golden_path.exists(), (
            f"Golden file missing: {golden_path}\nRun UPDATE_DRAFT_GOLDEN=1 make test to generate."
        )
        expected = loads_json5(golden_path.read_text())
        assert actual["system"] == expected["system"], "System prompt changed"
        assert actual["user"] == expected["user"], "User message changed"

    def test_round_2_prompt(self, golden_packs, oracle_cache):
        """Golden test for round 2 draft prompt (has prior pick)."""
        golden_path = GOLDEN_DIR / "round_2_pick.json5"
        already_picked = golden_packs[0]  # Angels

        system = build_draft_system_prompt(
            "You play to win. Evaluate every option by expected win rate.",
            seed=3,
            num_entrants=8,
        )
        user = build_draft_user_prompt(2, [golden_packs[1]], oracle_cache, already_picked=already_picked)

        actual = {"system": system, "user": user}

        if UPDATE_MODE:
            golden_path.parent.mkdir(parents=True, exist_ok=True)
            golden_path.write_text(dumps_json5(actual) + "\n")
            return

        assert golden_path.exists(), (
            f"Golden file missing: {golden_path}\nRun UPDATE_DRAFT_GOLDEN=1 make test to generate."
        )
        expected = loads_json5(golden_path.read_text())
        assert actual["system"] == expected["system"], "System prompt changed"
        assert actual["user"] == expected["user"], "User message changed"
