#!/usr/bin/env python3
"""Run a Jumpstart straight draft for the current tournament.

Reads the tournament entrants from data/tournaments/season-N.json, presents
each entrant's LLM with Jumpstart half-deck options (in straight-draft order),
and records picks + final decklists back to the tournament JSON.

Straight draft order for 8 players (2 rounds): 1,2,3,4,5,6,7,8,1,2,3,4,5,6,7,8
Higher seeds always pick first — no snake compensation.

Each pick shows all remaining packs in the pool, not a random subset.
A draft log (JSONL) with full LLM input/output is written incrementally
so partial results survive ctrl-c.

Usage:
    python scripts/tournament_draft.py
"""

import asyncio
import datetime
import json
import os
import random
import re
from pathlib import Path
from typing import IO
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI

from magebench.game import scryfall
from puppeteer.config import (
    PilotPlayer,
    load_personalities,
    load_prompts,
    load_toolsets,
    resolve_preset,
)
from puppeteer.decision_renderer import BASIC_LAND_NAMES
from puppeteer.jumpstart import HalfDeck, generate_dck, load_jumpstart_themes
from puppeteer.llm_cost import (
    get_model_price,
    llm_base_url,
    load_prices,
    required_api_key_env,
)
from scripts.draft_history import (
    CURRENT_DRAFT_HISTORY_VERSION,
    assert_current_draft_history_version,
)
from scripts.tournament_game import load_tournament

_ROOT = Path(__file__).resolve().parent.parent
_SEASON_FILE = _ROOT / "data" / "season.json"
_PRESETS_JSON = _ROOT / "puppeteer" / "presets.json"
_LOGS_DIR = Path.home() / ".mage-bench" / "logs"

PACKS_PER_PLAYER = 4
LLM_TIMEOUT_SECS = 900
MAX_TOKENS = 20_000
MAX_PICK_RETRIES = 10
_LOG_TZ = ZoneInfo("America/Los_Angeles")


def draft_order(num_entrants: int) -> list[int]:
    """Generate straight draft order (seeds) for 2 rounds.

    Round 1: 1, 2, ..., N
    Round 2: 1, 2, ..., N

    Higher seeds always pick first — no snake compensation.
    """
    forward = list(range(1, num_entrants + 1))
    return forward + forward


def _load_tournament() -> tuple[dict, Path]:
    """Load the current tournament JSON. Returns (tournament_data, file_path)."""
    # Delegate to shared implementation in tournament_game.py
    return load_tournament()


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

    assert card_name in oracle, f"Missing oracle text for draft card {card_name!r}"
    info = oracle[card_name]
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

    lines.extend(_format_card_line(card.name, card.count, oracle) for card in non_lands)
    lines.extend(_format_card_line(card.name, card.count, oracle) for card in lands)

    return "\n".join(lines)


def build_draft_system_prompt(
    personality_suffix: str | None,
    seed: int,
    num_entrants: int,
) -> str:
    """Build the system prompt for a draft pick."""
    prompt = (
        "You are drafting a Jumpstart deck for a Magic: The Gathering tournament.\n"
        "Jumpstart decks combine two 20-card half-deck packs into a 40-card deck.\n"
        "You will make 2 picks to form your 40-card deck.\n"
        "\n"
        f"This is a straight (linear) draft with {num_entrants} players.\n"
        f"Draft order: seeds 1, 2, ..., {num_entrants}, then 1, 2, ..., {num_entrants} again.\n"
        "Higher seeds always pick first — there is no snake compensation.\n"
        f"You are seed #{seed}.\n"
        "\n"
        "The full open pool of remaining packs is shown for each pick.\n"
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
            f"You already picked: {already_picked.theme}. Now pick a second half-deck to pair with it."
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

    # Check if the response is just a number
    if text.isdigit():
        n = int(text)
        if 1 <= n <= num_options:
            return n

    # Check for a leading number (possibly bold-wrapped like **17**).
    # Models put their answer first, then reasoning that may reference
    # other option numbers — so a leading number is the strongest signal.
    leading = re.match(r"\*{0,2}(\d{1,2})\*{0,2}(?:\s|$|[.),:])", text)
    if leading:
        n = int(leading.group(1))
        if 1 <= n <= num_options:
            return n

    # Look for explicit patterns: "Option 12", "pick #3", "choose 7"
    explicit = re.findall(
        r"(?:option|pick|choose|choice)\s*#?\s*(\d+)", text, re.IGNORECASE
    )
    valid_explicit = [int(m) for m in explicit if 1 <= int(m) <= num_options]
    if valid_explicit:
        return valid_explicit[0]

    # Fall back to first standalone number in valid range
    matches = re.findall(r"\b(\d{1,2})\b", text)
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
    toolsets: dict[str, list[str]],
) -> tuple[str, str, str | None, str | None]:
    """Resolve an entrant's locked config to (model, provider, reasoning_effort, personality_suffix).

    Uses the same preset resolution as the game pilot.
    """
    player = PilotPlayer(
        name=entrant["display_name"],
        preset=entrant["preset"],
        personality=entrant["personality"],
    )
    resolve_preset(player, presets_data, prompts, toolsets)

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
        player.provider,
        player.reasoning_effort,
        prompt_suffix,
    )


def _log_llm_call(
    log_file: IO[str],
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    response_json: dict,
    attempt: str,
    pick_meta: dict,
) -> None:
    """Write a raw LLM call record to the draft log, before any asserts."""
    entry = {
        "type": "llm_call",
        "ts": datetime.datetime.now(_LOG_TZ).isoformat(),
        "attempt": attempt,
        "model": model,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "response": response_json,
        **pick_meta,
    }
    log_file.write(json.dumps(entry) + "\n")
    log_file.flush()


def _extract_content(message: object) -> tuple[str | None, str | None]:
    """Extract (content, thinking) from an OpenAI chat completion message.

    Strips whitespace. Does NOT fall back to reasoning_content — reasoning
    text is internal deliberation full of option references that confuse
    parse_pick. If the model produced no content, the caller must retry.
    """
    content: str | None = getattr(message, "content", None)
    if content is not None:
        content = content.strip() or None
    thinking: str | None = getattr(message, "reasoning_content", None) or getattr(
        message, "reasoning", None
    )
    return content, thinking


def _build_attempt_record(
    attempt: int,
    content: str | None,
    thinking: str | None,
) -> dict:
    """Build one ordered LLM attempt record for draft history v2."""
    record: dict = {
        "attempt": attempt,
        "response": content,
        "accepted": False,
    }
    if thinking:
        record["thinking"] = thinking
    return record


async def _llm_pick(
    client: AsyncOpenAI,
    model: str,
    system_prompt: str,
    user_prompt: str,
    reasoning_effort: str | None,
    num_options: int,
    log_file: IO[str],
    pick_meta: dict,
) -> tuple[int, str, list[dict], dict]:
    """Call the LLM to make a draft pick.

    Returns (1-based pick, final response text, ordered attempts, usage dict).

    Retries up to MAX_PICK_RETRIES times if the model fails to produce a
    parseable bare-number pick (empty content, extra text, unparseable
    response, etc.). Each retry includes the full conversation history so the
    model can see what went wrong.
    """
    messages: list = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    create_kwargs: dict = {
        "model": model,
        "max_tokens": MAX_TOKENS,
    }
    if reasoning_effort is not None:
        create_kwargs["extra_body"] = {"reasoning": {"effort": reasoning_effort}}

    usage: dict = {"prompt_tokens": 0, "completion_tokens": 0}
    attempts: list[dict] = []

    for attempt in range(1, MAX_PICK_RETRIES + 1):
        attempt_label = "initial" if attempt == 1 else f"retry_{attempt - 1}"

        response = await asyncio.wait_for(
            client.chat.completions.create(messages=messages, **create_kwargs),
            timeout=LLM_TIMEOUT_SECS,
        )

        _log_llm_call(
            log_file,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_json=response.model_dump(),
            attempt=attempt_label,
            pick_meta=pick_meta,
        )

        assert response.choices, (
            f"LLM returned empty choices for model {model} (attempt {attempt})"
        )

        if response.usage:
            usage["prompt_tokens"] += response.usage.prompt_tokens or 0
            usage["completion_tokens"] += response.usage.completion_tokens or 0

        content, attempt_thinking = _extract_content(response.choices[0].message)
        attempt_record = _build_attempt_record(attempt, content, attempt_thinking)

        if not content:
            reason = "Your response was empty — no content was returned."
        else:
            try:
                pick = parse_pick(content, num_options)
                if content.isdigit():
                    attempt_record["accepted"] = True
                    attempts.append(attempt_record)
                    return pick, content, attempts, usage
                reason = "I parsed your pick, but your response included extra text."
            except ValueError:
                reason = f"Could not parse a valid option number (1-{num_options}) from your response."

        attempt_record["rejection_reason"] = reason
        attempts.append(attempt_record)

        # Append the failed response and a retry prompt to the conversation
        messages.append({"role": "assistant", "content": content or "(empty response)"})
        messages.append(
            {
                "role": "user",
                "content": (
                    f"{reason} Reply with ONLY a single number from 1 to {num_options}. No other text."
                ),
            }
        )
        print(f"    Attempt {attempt} failed: {reason} Retrying...")

    raise AssertionError(
        f"Model {model} failed to produce a valid pick after {MAX_PICK_RETRIES} attempts"
    )


async def run_draft(tournament: dict, tournament_path: Path) -> None:
    """Run the full straight draft, with resume support for partial drafts."""
    entrants = tournament["entrants"]
    num_entrants = len(entrants)
    entrants_by_seed = {e["seed"]: e for e in entrants}
    order = draft_order(num_entrants)

    # Early exit if draft is already complete
    if "draft" in tournament:
        assert_current_draft_history_version(tournament["draft"])
        existing_picks = tournament["draft"]["picks"]
        if len(existing_picks) >= len(order) and tournament["draft"].get("decklists"):
            print("Draft already complete. Nothing to do.")
            return

    # Load Jumpstart packs
    half_decks = load_jumpstart_themes(_ROOT)
    half_decks_by_theme = {hd.theme: hd for hd in half_decks}

    picks: list[dict]
    start_idx: int
    entrant_picks: dict[int, list[HalfDeck]] = {seed: [] for seed in entrants_by_seed}
    cumulative_cost = 0.0

    # Resume support: detect partial draft and reconstruct state
    if "draft" in tournament:
        picks = tournament["draft"]["picks"]

        # Reconstruct pool from saved theme list
        pool_themes = tournament["draft"]["pool"]
        available_packs = {t: half_decks_by_theme[t] for t in pool_themes}

        for pick in picks:
            entrant_picks[pick["seed"]].append(available_packs[pick["picked"]])
            del available_packs[pick["picked"]]
            cumulative_cost += pick.get("cost_usd", 0)

        start_idx = len(picks)
        print(
            f"Resuming draft from pick {start_idx + 1}/{len(order)} "
            f"({start_idx} picks completed, ${cumulative_cost:.4f} spent)"
        )
    else:
        # Fresh draft — sample a random pool
        pool_size = PACKS_PER_PLAYER * num_entrants
        assert len(half_decks) >= pool_size, (
            f"Need {pool_size} packs for draft but only {len(half_decks)} available"
        )
        pool = random.sample(half_decks, pool_size)
        available_packs = {hd.theme: hd for hd in pool}

        picks = []
        start_idx = 0

        tournament["draft"] = {
            "history_version": CURRENT_DRAFT_HISTORY_VERSION,
            "packs_per_player": PACKS_PER_PLAYER,
            "pool": sorted(available_packs.keys()),
            "picks": picks,
            "decklists": {},
        }
        # Flush immediately so the pool is saved even if we crash on pick 1
        tournament_path.write_text(json.dumps(tournament, indent=2) + "\n")
        print(f"Opened {pool_size} packs for draft (from {len(half_decks)} total)")

    # Fetch oracle text for remaining packs
    oracle = _fetch_oracle_texts(list(available_packs.values()))

    # Fetch prices for cost tracking
    prices = load_prices()

    # Resolve configs
    presets_data = json.loads(_PRESETS_JSON.read_text())
    personalities = load_personalities(None)
    prompts = load_prompts(None)
    toolsets = load_toolsets(None)

    # Set up incremental draft log
    ts = datetime.datetime.now(_LOG_TZ).strftime("%Y%m%d_%H%M%S")
    log_dir = _LOGS_DIR / f"draft_{ts}"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "draft.jsonl"
    print(f"Draft log: {log_path}")

    # Resolve all entrant configs upfront (each seed appears twice in draft)
    entrant_configs: dict[int, tuple[str, str, str | None, str | None]] = {}
    for entrant in entrants:
        entrant_configs[entrant["seed"]] = _resolve_entrant_config(
            entrant, presets_data, personalities, prompts, toolsets
        )

    # Set up API clients (one per unique key+provider)
    client_cache: dict[tuple[str, str], AsyncOpenAI] = {}
    for _model, provider, _, _ in entrant_configs.values():
        key_env = required_api_key_env(provider)
        api_key = os.environ.get(key_env)
        assert api_key, f"Missing API key: set {key_env} environment variable"
        cache_key = (api_key, provider)
        if cache_key not in client_cache:
            client_cache[cache_key] = AsyncOpenAI(
                api_key=api_key,
                base_url=llm_base_url(provider),
                timeout=LLM_TIMEOUT_SECS + 5,
                max_retries=1,
            )

    with open(log_path, "a") as log_file:
        for pick_idx, seed in enumerate(order):
            if pick_idx < start_idx:
                continue
            round_num = 1 if pick_idx < num_entrants else 2
            entrant = entrants_by_seed[seed]

            # All remaining packs are options (sorted for stable ordering)
            option_themes = sorted(available_packs.keys())
            options = [available_packs[t] for t in option_themes]

            model, provider, reasoning_effort, prompt_suffix = entrant_configs[seed]

            # Build prompts
            already_picked = entrant_picks[seed][0] if entrant_picks[seed] else None
            system_prompt = build_draft_system_prompt(prompt_suffix, seed, num_entrants)
            user_prompt = build_draft_user_prompt(
                round_num, options, oracle, already_picked
            )

            key_env = required_api_key_env(provider)
            api_key = os.environ.get(key_env)
            assert api_key, f"Missing API key: set {key_env} environment variable"
            client = client_cache[(api_key, provider)]

            print(
                f"\nPick {pick_idx + 1}/{len(order)}: "
                f"Seed #{seed} ({entrant['display_name']}) — "
                f"Round {round_num}, {len(option_themes)} packs available"
            )

            pick_meta = {
                "pick_idx": pick_idx,
                "seed": seed,
                "display_name": entrant["display_name"],
                "round": round_num,
            }
            pick_num, reasoning, attempts, usage = await _llm_pick(
                client,
                model,
                system_prompt,
                user_prompt,
                reasoning_effort,
                len(options),
                log_file,
                pick_meta,
            )

            picked_theme = option_themes[pick_num - 1]
            picked_pack = available_packs[picked_theme]
            entrant_picks[seed].append(picked_pack)

            # Remove picked pack from pool
            del available_packs[picked_theme]

            # Compute cost
            pick_cost = 0.0
            price = get_model_price(model, prices)
            if price and usage:
                input_price, output_price = price
                pick_cost = (
                    usage.get("prompt_tokens", 0) * input_price / 1_000_000
                    + usage.get("completion_tokens", 0) * output_price / 1_000_000
                )
            cumulative_cost += pick_cost

            print(
                f"  -> Picked: {picked_theme} "
                f"({usage.get('prompt_tokens', '?')}+{usage.get('completion_tokens', '?')} tok, "
                f"${pick_cost:.4f}, total ${cumulative_cost:.4f})"
            )

            pick_record: dict = {
                "seed": seed,
                "round": round_num,
                "options": option_themes,
                "picked": picked_theme,
                "reasoning": reasoning,
                "attempts": attempts,
                "usage": usage,
                "cost_usd": pick_cost,
            }
            picks.append(pick_record)

            # Write incremental JSONL log entry (parsed result)
            log_entry = {
                "type": "pick_result",
                **pick_record,
                "ts": datetime.datetime.now(_LOG_TZ).isoformat(),
                "pick_idx": pick_idx,
                "display_name": entrant["display_name"],
                "model": model,
                "cumulative_cost_usd": cumulative_cost,
            }
            log_file.write(json.dumps(log_entry) + "\n")
            log_file.flush()

            # Flush tournament JSON after each pick
            tournament_path.write_text(json.dumps(tournament, indent=2) + "\n")

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

    # Save final draft results with decklists
    tournament["draft"]["decklists"] = decklists
    tournament["draft"]["total_cost_usd"] = cumulative_cost
    tournament_path.write_text(json.dumps(tournament, indent=2) + "\n")
    print(f"\nDraft complete! Total cost: ${cumulative_cost:.4f}")
    print(f"Results saved to {tournament_path}")
    print(f"Full log: {log_path}")

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
