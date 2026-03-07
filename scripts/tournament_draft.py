#!/usr/bin/env python3
"""Run a Jumpstart snake draft for the current tournament.

Reads the tournament entrants from data/tournaments/season-N.json, presents
each entrant's LLM with Jumpstart half-deck options (in snake-draft order),
and records picks + final decklists back to the tournament JSON.

Snake draft order for 8 players (2 rounds): 1,2,3,4,5,6,7,8,8,7,6,5,4,3,2,1

Usage:
    python scripts/tournament_draft.py
"""

import asyncio
import json
import os
import random
import re
from pathlib import Path

from openai import AsyncOpenAI

from puppeteer.config import (
    PilotPlayer,
    resolve_preset,
    load_personalities,
    load_prompts,
)
from puppeteer.decision_renderer import BASIC_LAND_NAMES
from puppeteer.jumpstart import HalfDeck, generate_dck, load_jumpstart_themes
from puppeteer.llm_cost import DEFAULT_BASE_URL, required_api_key_env
from scripts import scryfall

_ROOT = Path(__file__).resolve().parent.parent
_SEASON_FILE = _ROOT / "data" / "season.json"
_PRESETS_JSON = _ROOT / "puppeteer" / "presets.json"

PACKS_PER_PICK = 4
LLM_TIMEOUT_SECS = 60
MAX_TOKENS = 2000


def snake_draft_order(num_entrants: int) -> list[int]:
    """Generate snake draft order (seeds) for 2 rounds.

    Round 1: 1, 2, ..., N
    Round 2: N, ..., 2, 1
    """
    forward = list(range(1, num_entrants + 1))
    backward = list(range(num_entrants, 0, -1))
    return forward + backward


def _load_tournament() -> tuple[dict, Path]:
    """Load the current tournament JSON. Returns (tournament_data, file_path)."""
    assert _SEASON_FILE.exists(), f"Season file not found: {_SEASON_FILE}"
    season_data = json.loads(_SEASON_FILE.read_text())
    assert season_data["phase"] == "tournament", (
        f"Season {season_data['current_season']} is in phase "
        f"'{season_data['phase']}', expected 'tournament'"
    )
    tournament_path = _ROOT / season_data["tournament"]
    assert tournament_path.exists(), f"Tournament file not found: {tournament_path}"
    tournament = json.loads(tournament_path.read_text())
    return tournament, tournament_path


def _fetch_oracle_texts(half_decks: list[HalfDeck]) -> dict[str, dict]:
    """Fetch oracle text for all non-land cards across all packs via Scryfall.

    Returns {card_name: oracle_fields} for all non-basic-land cards.
    """
    card_names: set[str] = set()
    for hd in half_decks:
        for card in hd.cards:
            if card.name not in BASIC_LAND_NAMES:
                card_names.add(card.name)

    if not card_names:
        return {}

    oracle = scryfall.get_oracle_texts(sorted(card_names))
    print(f"Fetched oracle text for {len(oracle)} cards")
    return oracle


def _format_card_line(card_name: str, count: int, oracle: dict[str, dict]) -> str:
    """Format a single card line for the draft prompt."""
    if card_name in BASIC_LAND_NAMES:
        return f"  {count}x {card_name} — Basic Land"

    info = oracle.get(card_name, {})
    parts = [f"  {count}x {card_name}"]
    if info.get("mana_cost"):
        parts.append(info["mana_cost"])
    parts_str = " ".join(parts)

    details = []
    if info.get("type_line"):
        details.append(info["type_line"])
    if info.get("oracle_text"):
        # Collapse multi-line oracle text to single line
        details.append(info["oracle_text"].replace("\n", " / "))
    if info.get("power") is not None:
        details.append(f"{info['power']}/{info['toughness']}")

    if details:
        return f"{parts_str} — {' — '.join(details)}"
    return parts_str


def _format_pack_option(
    option_num: int,
    half_deck: HalfDeck,
    oracle: dict[str, dict],
) -> str:
    """Format a single pack option for the draft prompt."""
    lines = [f"Option {option_num}: {half_deck.theme}"]

    # Group cards: non-lands first, then lands
    non_lands = []
    lands = []
    for card in half_deck.cards:
        if card.name in BASIC_LAND_NAMES:
            lands.append(card)
        else:
            non_lands.append(card)

    for card in non_lands:
        lines.append(_format_card_line(card.name, card.count, oracle))
    for card in lands:
        lines.append(_format_card_line(card.name, card.count, oracle))

    return "\n".join(lines)


def build_draft_system_prompt(personality_suffix: str | None) -> str:
    """Build the system prompt for a draft pick."""
    prompt = (
        "You are drafting a Jumpstart deck for a Magic: The Gathering tournament.\n"
        "Jumpstart decks combine two 20-card half-deck packs into a 40-card deck.\n"
        "Pick the pack that best complements your strategy."
    )
    if personality_suffix:
        prompt += f"\n\n{personality_suffix}"
    return prompt


def build_draft_user_prompt(
    round_num: int,
    options: list[HalfDeck],
    oracle: dict[str, dict],
    already_picked: HalfDeck | None = None,
) -> str:
    """Build the user prompt for a draft pick."""
    lines = []

    if already_picked:
        lines.append(
            f"You already picked: {already_picked.theme}. "
            f"Now pick a second half-deck to pair with it."
        )
        lines.append("")

    lines.append(
        f"Pick {round_num} of 2 — choose a half-deck pack for your tournament deck."
    )
    lines.append("")

    for i, hd in enumerate(options, 1):
        lines.append(_format_pack_option(i, hd, oracle))
        lines.append("")

    lines.append(f"Reply with ONLY the number of your choice (1-{len(options)}).")
    return "\n".join(lines)


def parse_pick(response_text: str, num_options: int) -> int:
    """Parse the LLM's pick from its response text.

    Returns 1-based option number.
    """
    text = response_text.strip()

    # Try to find a single digit that's a valid option
    # First, check if the response is just a number
    if text.isdigit():
        n = int(text)
        if 1 <= n <= num_options:
            return n

    # Look for patterns like "1", "Option 1", "I choose 1", "pick #1"
    matches = re.findall(r"\b([1-9])\b", text)
    valid = [int(m) for m in matches if 1 <= int(m) <= num_options]
    if len(valid) == 1:
        return valid[0]

    # If multiple valid numbers, take the first one (most likely the answer)
    if valid:
        return valid[0]

    # Could not parse
    raise ValueError(f"Could not parse pick from response: {text!r}")


def _resolve_entrant_config(
    entrant: dict,
    presets_data: dict,
    personalities: dict[str, dict],
    prompts: dict[str, str],
) -> tuple[str, str, str | None, str | None]:
    """Resolve an entrant's locked config to (model, base_url, reasoning_effort, personality_suffix).

    Uses the same preset resolution as the game pilot.
    """
    player = PilotPlayer(
        name=entrant["display_name"],
        preset=entrant["preset"],
        personality=entrant["personality"],
    )
    resolve_preset(player, presets_data, prompts)

    assert player.model is not None, (
        f"Preset {entrant['preset']!r} did not resolve a model"
    )

    # Get personality prompt_suffix
    personality_name = entrant["personality"]
    assert personality_name in personalities, (
        f"Personality {personality_name!r} not found in personalities"
    )
    prompt_suffix = personalities[personality_name].get("prompt_suffix")

    return (
        player.model,
        DEFAULT_BASE_URL,
        player.reasoning_effort,
        prompt_suffix,
    )


async def _llm_pick(
    client: AsyncOpenAI,
    model: str,
    system_prompt: str,
    user_prompt: str,
    reasoning_effort: str | None,
    num_options: int,
) -> tuple[int, str]:
    """Call the LLM to make a draft pick. Returns (1-based pick, reasoning text)."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    create_kwargs: dict = dict(
        model=model,
        messages=messages,
        max_tokens=MAX_TOKENS,
    )
    if reasoning_effort is not None:
        create_kwargs["extra_body"] = {"reasoning": {"effort": reasoning_effort}}

    response = await asyncio.wait_for(
        client.chat.completions.create(**create_kwargs),
        timeout=LLM_TIMEOUT_SECS,
    )

    assert response.choices, f"LLM returned empty choices for model {model}"
    content = response.choices[0].message.content
    assert content is not None, f"LLM returned None content for model {model}"

    try:
        pick = parse_pick(content, num_options)
    except ValueError:
        # Retry once with a clearer prompt
        messages.append({"role": "assistant", "content": content})
        messages.append(
            {
                "role": "user",
                "content": f"Please reply with ONLY a single number from 1 to {num_options}.",
            }
        )
        retry_response = await asyncio.wait_for(
            client.chat.completions.create(**create_kwargs | {"messages": messages}),
            timeout=LLM_TIMEOUT_SECS,
        )
        assert retry_response.choices, (
            f"LLM returned empty choices on retry for model {model}"
        )
        retry_content = retry_response.choices[0].message.content
        assert retry_content is not None, (
            f"LLM returned None content on retry for model {model}"
        )
        pick = parse_pick(retry_content, num_options)

    return pick, content


async def run_draft(tournament: dict, tournament_path: Path) -> None:
    """Run the full snake draft."""
    assert "draft" not in tournament, (
        "Tournament already has draft results. Delete the 'draft' key to re-run."
    )

    entrants = tournament["entrants"]
    num_entrants = len(entrants)
    entrants_by_seed = {e["seed"]: e for e in entrants}

    # Load Jumpstart packs and oracle text
    half_decks = load_jumpstart_themes(_ROOT)
    oracle = _fetch_oracle_texts(half_decks)

    # Build seed-indexed pack pool
    available_packs = {hd.theme: hd for hd in half_decks}
    print(f"Loaded {len(available_packs)} Jumpstart packs")

    # Resolve configs
    presets_data = json.loads(_PRESETS_JSON.read_text())
    personalities = load_personalities(None)
    prompts = load_prompts(None)

    # Snake draft
    order = snake_draft_order(num_entrants)
    picks: list[dict] = []
    entrant_picks: dict[int, list[HalfDeck]] = {seed: [] for seed in entrants_by_seed}
    client_cache: dict[tuple[str, str], AsyncOpenAI] = {}

    for pick_idx, seed in enumerate(order):
        round_num = 1 if pick_idx < num_entrants else 2
        entrant = entrants_by_seed[seed]

        # Select random options from available packs
        available_themes = list(available_packs.keys())
        assert len(available_themes) >= PACKS_PER_PICK, (
            f"Only {len(available_themes)} packs left, need {PACKS_PER_PICK}"
        )
        option_themes = random.sample(available_themes, PACKS_PER_PICK)
        options = [available_packs[t] for t in option_themes]

        # Resolve entrant config
        model, base_url, reasoning_effort, prompt_suffix = _resolve_entrant_config(
            entrant, presets_data, personalities, prompts
        )

        # Build prompts
        already_picked = entrant_picks[seed][0] if entrant_picks[seed] else None
        system_prompt = build_draft_system_prompt(prompt_suffix)
        user_prompt = build_draft_user_prompt(
            round_num, options, oracle, already_picked
        )

        # Get API key
        key_env = required_api_key_env(base_url)
        api_key = os.environ.get(key_env, "")
        assert api_key, f"Missing API key: set {key_env} environment variable"

        # Get or create cached client
        cache_key = (api_key, base_url)
        if cache_key not in client_cache:
            client_cache[cache_key] = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=LLM_TIMEOUT_SECS + 5,
                max_retries=1,
            )
        client = client_cache[cache_key]

        print(
            f"Pick {pick_idx + 1}/{len(order)}: "
            f"Seed #{seed} ({entrant['display_name']}) — "
            f"Round {round_num}, options: {option_themes}"
        )

        pick_num, reasoning = await _llm_pick(
            client, model, system_prompt, user_prompt, reasoning_effort, PACKS_PER_PICK
        )

        picked_theme = option_themes[pick_num - 1]
        picked_pack = available_packs[picked_theme]
        entrant_picks[seed].append(picked_pack)

        # Remove picked pack from pool
        del available_packs[picked_theme]

        print(f"  -> Picked: {picked_theme}")

        picks.append(
            {
                "seed": seed,
                "round": round_num,
                "options": option_themes,
                "picked": picked_theme,
                "reasoning": reasoning,
            }
        )

    # Build decklists
    decklists: dict[str, dict] = {}
    for seed, picked_packs in entrant_picks.items():
        assert len(picked_packs) == 2, (
            f"Seed #{seed} has {len(picked_packs)} picks, expected 2"
        )
        half1, half2 = picked_packs
        dck_content = generate_dck(half1, half2)
        card_lines = [
            line
            for line in dck_content.splitlines()
            if line and not line.startswith("NAME:")
        ]
        decklists[str(seed)] = {
            "half_decks": [half1.theme, half2.theme],
            "cards": card_lines,
        }

    # Save draft results
    tournament["draft"] = {
        "packs_per_pick": PACKS_PER_PICK,
        "picks": picks,
        "decklists": decklists,
    }

    tournament_path.write_text(json.dumps(tournament, indent=2) + "\n")
    print(f"\nDraft complete! Results saved to {tournament_path}")

    # Print summary
    print("\nDecklists:")
    for seed in sorted(entrant_picks.keys()):
        entrant = entrants_by_seed[seed]
        dl = decklists[str(seed)]
        print(
            f"  #{seed} {entrant['display_name']}: {dl['half_decks'][0]} + {dl['half_decks'][1]}"
        )


def main() -> int:
    tournament, tournament_path = _load_tournament()
    asyncio.run(run_draft(tournament, tournament_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
