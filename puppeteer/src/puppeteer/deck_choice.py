"""Deck choice: let LLMs pick their deck from format-legal options."""

import os
import re
from pathlib import Path

from openai import OpenAI

from puppeteer.config import _DECK_TYPE_TO_DIR, PilotPlayer
from puppeteer.llm_cost import DEFAULT_BASE_URL, required_api_key_env
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


def _deck_display_name(path: Path) -> str:
    """Convert a deck filename to a display name (strip .dck extension)."""
    return path.stem


def _summarize_deck(deck_path: Path) -> str:
    """Return top 5 nonland maindeck cards by count as a compact string."""
    cards: list[tuple[int, str]] = []
    text = deck_path.read_text()
    for line in text.splitlines():
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


def list_available_decks(project_root: Path, deck_type: str) -> list[tuple[Path, str]]:
    """Find all .dck files for the format. Returns (relative_path, display_name) sorted by name."""
    dir_name = _DECK_TYPE_TO_DIR.get(deck_type, "Commander")
    deck_dir = project_root / "Mage.Client" / "release" / "sample-decks" / dir_name
    decks = []
    for p in deck_dir.rglob("*.dck"):
        rel = p.relative_to(project_root)
        decks.append((rel, _deck_display_name(p)))
    decks.sort(key=lambda x: x[1].lower())
    return decks


def _build_choice_prompt(
    decks: list[tuple[Path, str]],
    project_root: Path,
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
    for i, (rel_path, display_name) in enumerate(decks, 1):
        if include_summaries:
            summary = _summarize_deck(project_root / rel_path)
            lines.append(f"  {i}. {display_name} ({summary})")
        else:
            lines.append(f"  {i}. {display_name}")

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
    decks: list[tuple[Path, str]],
    project_root: Path,
    deck_type: str,
    already_chosen: list[tuple[str, str]],
) -> tuple[Path, str]:
    """Ask one player's LLM to choose a deck. Returns (relative_path, display_name)."""
    base_url = player.base_url or DEFAULT_BASE_URL
    key_env = required_api_key_env(base_url)
    api_key = os.environ[key_env]

    client = OpenAI(base_url=base_url, api_key=api_key)
    prompt = _build_choice_prompt(decks, project_root, player.name, already_chosen, deck_type)

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
    deck_type: str,
) -> None:
    """Resolve deck='choice' for pilot players by asking their LLMs."""
    choice_players = [p for p in players if p.deck == "choice"]
    if not choice_players:
        return

    all_decks = list_available_decks(project_root, deck_type)
    assert all_decks, f"No .dck files found for deck type {deck_type!r}"

    available = list(all_decks)
    already_chosen: list[tuple[str, str]] = []

    for player in choice_players:
        assert player.model, f"Player {player.name!r} has deck='choice' but no model set"
        path, display_name = choose_deck_for_player(
            player,
            available,
            project_root,
            deck_type,
            already_chosen,
        )
        player.deck = str(path)
        logger.info("Deck choice for %s: %s", player.name, display_name)
        already_chosen.append((player.name, display_name))
        # Remove chosen deck from pool
        available = [(p, n) for p, n in available if p != path]
        if not available:
            # All decks used — reset pool (allows more players than decks)
            available = list(all_decks)
