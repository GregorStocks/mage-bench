"""Tests for deck choice logic."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from puppeteer.config import DeckEntry, PilotPlayer
from puppeteer.deck_choice import (
    _build_choice_prompt,
    _parse_card_name,
    _parse_choice,
    _summarize_entry,
    list_available_decks,
    resolve_choice_decks,
)

# --- _parse_card_name ---


def test_parse_card_name_maindeck():
    count, name, sb = _parse_card_name("4 [M21:1] Lightning Bolt")
    assert count == 4
    assert name == "Lightning Bolt"
    assert sb is False


def test_parse_card_name_sideboard():
    count, name, sb = _parse_card_name("SB: 1 [FRF:87] Tasigur, the Golden Fang")
    assert count == 1
    assert name == "Tasigur, the Golden Fang"
    assert sb is True


def test_parse_card_name_unparseable():
    assert _parse_card_name("") is None
    assert _parse_card_name("# comment") is None
    assert _parse_card_name("NAME:Burn") is None


def test_parse_card_name_multiword_set():
    count, name, sb = _parse_card_name("2 [CSP:152] Snow-Covered Island")
    assert count == 2
    assert name == "Snow-Covered Island"
    assert sb is False


# --- _summarize_entry ---


def test_summarize_entry_top_5():
    entry = DeckEntry(
        name="Burn",
        strategy="",
        cards=[
            "4 [M21:1] Lightning Bolt",
            "3 [M21:2] Goblin Guide",
            "2 [M21:3] Eidolon of the Great Revel",
            "2 [M21:4] Monastery Swiftspear",
            "1 [M21:5] Searing Blaze",
            "1 [M21:6] Lava Spike",
            "10 [M21:7] Mountain",  # basic land, excluded
        ],
    )
    result = _summarize_entry(entry)
    assert "Lightning Bolt" in result
    assert "Goblin Guide" in result
    assert "Eidolon of the Great Revel" in result
    assert "Monastery Swiftspear" in result
    # When counts tie, alphabetical order wins: Lava Spike before Searing Blaze
    assert "Lava Spike" in result
    assert "Searing Blaze" not in result  # 6th card, excluded
    assert "Mountain" not in result  # basic land


def test_summarize_entry_excludes_basic_lands():
    entry = DeckEntry(
        name="Control",
        strategy="",
        cards=[
            "20 [M21:1] Island",
            "10 [CSP:1] Snow-Covered Forest",
            "4 [M21:2] Counterspell",
        ],
    )
    result = _summarize_entry(entry)
    assert "Island" not in result
    assert "Snow-Covered Forest" not in result
    assert "4x Counterspell" in result


def test_summarize_entry_excludes_sideboard():
    entry = DeckEntry(
        name="Burn",
        strategy="",
        cards=[
            "4 [M21:1] Lightning Bolt",
            "SB: 4 [M21:2] Pyroblast",
        ],
    )
    result = _summarize_entry(entry)
    assert "Lightning Bolt" in result
    assert "Pyroblast" not in result


# --- list_available_decks ---


def _make_registry(root: Path, fmt_dir: str, decks: dict[str, list[str]]) -> None:
    """Create a deck registry directory with JSON files."""
    reg_dir = root / "data" / "decks" / fmt_dir
    reg_dir.mkdir(parents=True)
    for name, cards in decks.items():
        slug = name.lower().replace(" ", "-")
        data = {"name": name, "strategy": "", "cards": cards}
        (reg_dir / f"{slug}.json").write_text(json.dumps(data))


def test_list_available_decks():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_registry(
            root,
            "legacy",
            {
                "Burn": ["4 [M21:1] Lightning Bolt"],
                "Delver": ["4 [ISD:1] Delver of Secrets"],
            },
        )
        result = list_available_decks(root, "Constructed - Legacy")
        assert len(result) == 2
        names = [e.name for e in result]
        assert names == ["Burn", "Delver"]  # sorted alphabetically


def test_list_available_decks_sorted():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_registry(
            root,
            "commander",
            {
                "Zurgo": ["1 [CMD:1] Sol Ring"],
                "Alpha": ["1 [CMD:1] Sol Ring"],
                "Middle": ["1 [CMD:1] Sol Ring"],
            },
        )
        result = list_available_decks(root, "Variant Magic - Commander")
        names = [e.name for e in result]
        assert names == ["Alpha", "Middle", "Zurgo"]


# --- _build_choice_prompt ---


def test_build_choice_prompt_small_pool():
    entries = [
        DeckEntry(name="Burn", strategy="", cards=["4 [M21:1] Lightning Bolt", "3 [M21:2] Goblin Guide"]),
        DeckEntry(name="Delver", strategy="", cards=["4 [ISD:1] Delver of Secrets"]),
    ]
    prompt = _build_choice_prompt(entries, "TestBot", [], "Constructed - Legacy")
    assert "TestBot" in prompt
    assert "Constructed - Legacy" in prompt
    assert "1. Burn" in prompt
    assert "2. Delver" in prompt
    # Small pool includes summaries
    assert "Lightning Bolt" in prompt
    assert "Delver of Secrets" in prompt
    assert "ONLY the number" in prompt


def test_build_choice_prompt_already_chosen():
    entries = [
        DeckEntry(name="Zurgo", strategy="", cards=["1 [CMD:1] Sol Ring"]),
    ]
    already = [("Player1", "Burn"), ("Player2", "Delver")]
    prompt = _build_choice_prompt(entries, "TestBot", already, "Variant Magic - Commander")
    assert "Player1: Burn" in prompt
    assert "Player2: Delver" in prompt


def test_build_choice_prompt_large_pool_no_summaries():
    entries = [DeckEntry(name=f"Deck{i:02d}", strategy="", cards=[f"4 [M21:1] Card{i}"]) for i in range(35)]
    prompt = _build_choice_prompt(entries, "TestBot", [], "Constructed - Legacy")
    # Large pool: names only, no card summaries
    assert "1. Deck00" in prompt
    assert "Card" not in prompt


# --- _parse_choice ---


def test_parse_choice_simple():
    assert _parse_choice("3", 5) == 2  # 0-based


def test_parse_choice_text_with_number():
    assert _parse_choice("I choose deck number 2.", 5) == 1


def test_parse_choice_first_number_wins():
    assert _parse_choice("I like 4 and 2", 5) == 3  # takes 4, 0-based = 3


def test_parse_choice_out_of_range_crashes():
    with pytest.raises(AssertionError, match="out of range"):
        _parse_choice("10", 5)


def test_parse_choice_zero_crashes():
    with pytest.raises(AssertionError, match="out of range"):
        _parse_choice("0", 5)


def test_parse_choice_no_number_crashes():
    with pytest.raises(AssertionError, match="No number found"):
        _parse_choice("I can't decide", 5)


# --- resolve_choice_decks ---


def test_resolve_choice_decks_sets_player_deck():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_registry(
            root,
            "commander",
            {
                "Alpha": ["1 [CMD:1] Sol Ring"],
                "Beta": ["1 [CMD:1] Command Tower"],
            },
        )

        player = PilotPlayer(name="TestBot", deck="choice", model="test/model", base_url="https://openrouter.ai/api/v1")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "1"

        with (
            patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}),
            patch("puppeteer.deck_choice.OpenAI") as mock_openai,
        ):
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client

            resolve_choice_decks([player], root, "Variant Magic - Commander")

        assert player.deck is not None
        assert player.deck.endswith(".dck")
        assert player.deck_name == "Alpha"


def test_resolve_choice_decks_no_duplicates():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_registry(
            root,
            "commander",
            {
                "Alpha": ["1 [CMD:1] Sol Ring"],
                "Beta": ["1 [CMD:1] Command Tower"],
                "Gamma": ["1 [CMD:1] Arcane Signet"],
            },
        )

        p1 = PilotPlayer(name="Bot1", deck="choice", model="test/model", base_url="https://openrouter.ai/api/v1")
        p2 = PilotPlayer(name="Bot2", deck="choice", model="test/model", base_url="https://openrouter.ai/api/v1")

        def make_response(text):
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = text
            return resp

        with (
            patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}),
            patch("puppeteer.deck_choice.OpenAI") as mock_openai,
        ):
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = [
                make_response("1"),
                make_response("1"),
            ]
            mock_openai.return_value = mock_client

            resolve_choice_decks([p1, p2], root, "Variant Magic - Commander")

        assert p1.deck_name != p2.deck_name
        assert p1.deck_name == "Alpha"
        assert p2.deck_name == "Beta"


def test_resolve_choice_decks_skips_non_choice():
    """Players with deck != 'choice' should be untouched."""
    p1 = PilotPlayer(name="Bot1", deck="random", model="test/model")
    p2 = PilotPlayer(name="Bot2", deck="some/path.dck", model="test/model")

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_registry(root, "commander", {"Zurgo": ["1 [CMD:1] Sol Ring"]})
        resolve_choice_decks([p1, p2], root, "Variant Magic - Commander")

    assert p1.deck == "random"
    assert p2.deck == "some/path.dck"


def test_resolve_choice_decks_no_model_crashes():
    """Player with deck='choice' but no model should crash."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_registry(root, "commander", {"Zurgo": ["1 [CMD:1] Sol Ring"]})

        player = PilotPlayer(name="NoModel", deck="choice")
        with pytest.raises(AssertionError, match="no model set"):
            resolve_choice_decks([player], root, "Variant Magic - Commander")
