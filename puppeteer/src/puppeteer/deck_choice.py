"""Deck choice: let LLMs pick their deck from format-legal options."""

import os
import re
from pathlib import Path

from openai import OpenAI

from puppeteer.config import (
    _DECK_TYPE_TO_FORMAT_DIR,
    DeckEntry,
    PilotPlayer,
    generate_dck_file,
    load_deck_registry,
)
from puppeteer.llm_cost import llm_base_url, required_api_key_env
from puppeteer.log import get_logger

logger = get_logger(__name__)

# Basic lands excluded from deck summaries
_BASIC_LANDS = frozenset(
    {
        "Plains",
        "Island",
        "Swamp",
        "Mountain",
        "Forest",
        "Snow-Covered Plains",
        "Snow-Covered Island",
        "Snow-Covered Swamp",
        "Snow-Covered Mountain",
        "Snow-Covered Forest",
        "Wastes",
    }
)

# Regex for .dck lines: count [SET:NUM] Card Name
_CARD_LINE_RE = re.compile(r"^(\d+)\s+\[.*?\]\s+(.+)$")


def _parse_card_name(line: str) -> tuple[int, str, bool] | None:
    """Parse a .dck line into (count, card_name, is_sideboard).

    Returns None for unparseable lines.
    """
    stripped = line.strip()
    is_sideboard = stripped.startswith("SB:")
    if is_sideboard:
        stripped = stripped[3:].strip()
    m = _CARD_LINE_RE.match(stripped)
    if not m:
        return None
    return int(m.group(1)), m.group(2).strip(), is_sideboard


def _summarize_entry(entry: DeckEntry) -> str:
    """Return top 5 nonland maindeck cards by count as a compact string."""
    cards: list[tuple[int, str]] = []
    for line in entry.cards:
        parsed = _parse_card_name(line)
        if parsed is None:
            continue
        count, name, is_sideboard = parsed
        if is_sideboard:
            continue
        if name in _BASIC_LANDS:
            continue
        cards.append((count, name))

    # Sort by count descending, then alphabetically for ties
    cards.sort(key=lambda x: (-x[0], x[1]))
    top = cards[:5]
    return ", ".join(f"{count}x {name}" for count, name in top)


def list_available_decks(project_root: Path, deck_type: str) -> list[DeckEntry]:
    """Load all decks for the format from the registry. Returns entries sorted by name."""
    format_dir = _DECK_TYPE_TO_FORMAT_DIR.get(deck_type)
    assert format_dir, f"Unknown deck type for registry: {deck_type!r}"
    entries = load_deck_registry(project_root, format_dir)
    entries.sort(key=lambda e: e.name.lower())
    return entries


def _build_choice_prompt(
    decks: list[DeckEntry],
    player_name: str,
    already_chosen: list[tuple[str, str]],
    deck_type: str,
) -> str:
    """Build the user message for deck choice."""
    lines = [f"You are {player_name}. Choose a deck for a {deck_type} game."]
    lines.append("")

    if already_chosen:
        lines.append("Already chosen by other players:")
        for name, deck_name in already_chosen:
            lines.append(f"  - {name}: {deck_name}")
        lines.append("")

    lines.append("Available decks:")
    include_summaries = len(decks) < 30
    for i, entry in enumerate(decks, 1):
        if include_summaries:
            summary = _summarize_entry(entry)
            lines.append(f"  {i}. {entry.name} ({summary})")
        else:
            lines.append(f"  {i}. {entry.name}")

    lines.append("")
    lines.append("Reply with ONLY the number of your choice.")
    return "\n".join(lines)


def _parse_choice(response_text: str, num_decks: int) -> int:
    """Extract deck choice from LLM response. Returns 0-based index."""
    numbers = re.findall(r"\d+", response_text)
    assert numbers, f"No number found in LLM response: {response_text!r}"
    choice = int(numbers[0])
    assert 1 <= choice <= num_decks, f"Choice {choice} out of range 1-{num_decks} in response: {response_text!r}"
    return choice - 1


def choose_deck_for_player(
    player: PilotPlayer,
    decks: list[DeckEntry],
    deck_type: str,
    already_chosen: list[tuple[str, str]],
) -> DeckEntry:
    """Ask one player's LLM to choose a deck. Returns the chosen DeckEntry."""
    key_env = required_api_key_env(player.provider)
    api_key = os.environ[key_env]
    base_url = llm_base_url(player.provider)

    client = OpenAI(base_url=base_url, api_key=api_key)
    prompt = _build_choice_prompt(decks, player_name=player.name, already_chosen=already_chosen, deck_type=deck_type)

    assert player.model is not None
    response = client.chat.completions.create(
        model=player.model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=32,
        temperature=0.7,
    )
    text = response.choices[0].message.content
    assert text is not None, f"LLM returned empty content for {player.name}"
    idx = _parse_choice(text, len(decks))
    return decks[idx]


def resolve_choice_decks(
    players: list[PilotPlayer],
    project_root: Path,
    deck_type: str | None,
) -> None:
    """Resolve deck='choice' for pilot players by asking their LLMs."""
    choice_players = [p for p in players if p.deck == "choice"]
    if not choice_players:
        return

    assert deck_type is not None, "deck_type must be set when players use deck='choice'"
    all_decks = list_available_decks(project_root, deck_type)
    assert all_decks, f"No decks found in registry for deck type {deck_type!r}"

    available = list(all_decks)
    already_chosen: list[tuple[str, str]] = []

    for player in choice_players:
        assert player.model, f"Player {player.name!r} has deck='choice' but no model set"
        entry = choose_deck_for_player(
            player,
            available,
            deck_type,
            already_chosen,
        )
        dck_path = generate_dck_file(project_root, entry)
        player.deck = str(dck_path)
        player.deck_name = entry.name
        player.deck_strategy = entry.strategy
        logger.info("Deck choice for %s: %s", player.name, entry.name)
        already_chosen.append((player.name, entry.name))
        # Remove chosen deck from pool
        available = [e for e in available if e.name != entry.name]
        if not available:
            # All decks used — reset pool (allows more players than decks)
            available = list(all_decks)
