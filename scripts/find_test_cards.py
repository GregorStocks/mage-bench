#!/usr/bin/env python3
"""Find compact cards for golden-test scenarios using Scryfall."""

from __future__ import annotations

import argparse
from typing import NamedTuple

from magebench.game import scryfall


class Recipe(NamedTuple):
    name: str
    summary: str
    query: str


RECIPES = (
    Recipe(
        name="free-mana",
        summary="One-shot mana bursts that skip setup turns.",
        query=(
            "game:paper unique:cards order:edhrec "
            "(t:artifact or t:creature) mv<=1 "
            '(o:"Add one mana of any color" or '
            'o:"Add three mana of any one color" or '
            'o:"Add {C}{C}")'
        ),
    ),
    Recipe(
        name="zero-mana-body",
        summary="Free creatures that create battlefield presence immediately.",
        query="game:paper unique:cards order:cmc direction:asc t:creature mv=0",
    ),
    Recipe(
        name="clone-effect",
        summary="Cheap clone cards for copy-target scenarios.",
        query=("game:paper unique:cards order:cmc direction:asc function:clone is:spell mv<=4"),
    ),
    Recipe(
        name="trigger-prompt",
        summary="Permanents with explicit triggered abilities for prompt coverage.",
        query=("game:paper unique:cards order:cmc direction:asc is:permanent mv<=4 (o:/^When/ or o:/^Whenever/)"),
    ),
    Recipe(
        name="stack-interaction",
        summary="Cheap removal and countermagic for stack-state tests.",
        query=(
            "game:paper unique:cards order:cmc direction:asc is:spell mv<=2 (function:removal or function:counterspell)"
        ),
    ),
    Recipe(
        name="graveyard-setup",
        summary="Single-card graveyard enablers that stay compact.",
        query=(
            'game:paper unique:cards order:cmc direction:asc mv<=3 (o:"mill" or o:"discard" or o:"into your graveyard")'
        ),
    ),
    Recipe(
        name="weird-frames",
        summary="Cards with MDFC, suspend, split, or transform presentation.",
        query=("game:paper unique:cards order:cmc direction:asc (is:mdfc or is:split or is:transform or o:suspend)"),
    ),
    Recipe(
        name="clean-combat-body",
        summary="Low-text creatures for deterministic combat scripts.",
        query=(
            "game:paper unique:cards order:cmc direction:asc "
            "t:creature mv<=2 pow>=2 tou<=2 "
            "-o:/^When/ -o:/^Whenever/ -o:/dies/"
        ),
    ),
)

RECIPE_BY_NAME = {recipe.name: recipe for recipe in RECIPES}


def build_query(*, recipe: Recipe | None, raw_query: str | None, extra_filter: str | None) -> str:
    base_query = recipe.query if recipe is not None else raw_query
    assert base_query is not None, "build_query requires a recipe or raw query"
    query = " ".join(base_query.split())
    if extra_filter:
        query = f"{query} {' '.join(extra_filter.split())}"
    return query


def _string_field(card: dict, key: str) -> str:
    assert key in card, f"missing required field: {key}"
    value = card[key]
    assert isinstance(value, str), f"{key}: expected string, got {type(value).__name__}"
    return value


def _card_faces(card: dict) -> list[dict]:
    faces = card.get("card_faces")
    if not faces:
        return []
    assert isinstance(faces, list), f"card_faces: expected list, got {type(faces).__name__}"
    result: list[dict] = []
    for index, face in enumerate(faces):
        assert isinstance(face, dict), f"card_faces[{index}]: expected object, got {type(face).__name__}"
        result.append(face)
    return result


def _flatten_oracle(text: str) -> str:
    return " / ".join(part.strip() for part in text.splitlines() if part.strip())


def oracle_summary(card: dict) -> str:
    faces = _card_faces(card)
    if faces:
        parts = []
        for face in faces:
            name = _string_field(face, "name")
            mana = _string_field(face, "mana_cost")
            type_line = _string_field(face, "type_line")
            oracle = _flatten_oracle(_string_field(face, "oracle_text"))
            header = f"{name} {mana}".strip()
            if type_line:
                header = f"{header} -- {type_line}"
            if oracle:
                header = f"{header}: {oracle}"
            parts.append(header)
        return " // ".join(parts)
    return _flatten_oracle(_string_field(card, "oracle_text"))


def format_card(card: dict, index: int) -> str:
    name = _string_field(card, "name")
    mana_cost = _string_field(card, "mana_cost")
    type_line = _string_field(card, "type_line")
    set_code = _string_field(card, "set").upper()
    collector_number = _string_field(card, "collector_number")

    header = f"{index}. {name}"
    if mana_cost:
        header = f"{header} {mana_cost}"
    if type_line:
        header = f"{header} [{type_line}]"
    if set_code and collector_number:
        header = f"{header} ({set_code}:{collector_number})"

    oracle = oracle_summary(card)
    if not oracle:
        return header
    return f"{header}\n   {oracle}"


def search_cards(query: str, limit: int) -> list[dict]:
    assert limit > 0, f"limit must be positive, got {limit}"
    results = scryfall.search(query)
    cards: list[dict] = []
    for index, card in enumerate(results[:limit]):
        assert isinstance(card, dict), f"search result {index}: expected object, got {type(card).__name__}"
        cards.append(card)
    return cards


def print_recipe_list() -> None:
    print("Recipes:")
    for recipe in RECIPES:
        print(f"- {recipe.name}: {recipe.summary}")
        print(f"  {recipe.query}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Find compact cards for golden-test scenarios with Scryfall")
    parser.add_argument(
        "--list-recipes",
        action="store_true",
        help="List built-in recipe queries and exit",
    )
    parser.add_argument(
        "--recipe",
        choices=sorted(RECIPE_BY_NAME),
        help="Use a built-in query recipe",
    )
    parser.add_argument("--query", help="Run a raw Scryfall query string")
    parser.add_argument(
        "--filter",
        dest="extra_filter",
        help="Extra query terms appended after the base recipe/query",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=8,
        help="Maximum number of cards to print",
    )
    args = parser.parse_args()

    if args.list_recipes:
        if args.recipe or args.query or args.extra_filter:
            parser.error("--list-recipes does not combine with search arguments")
        print_recipe_list()
        return

    if (args.recipe is None) == (args.query is None):
        parser.error("choose exactly one of --recipe or --query")
    if args.limit <= 0:
        parser.error("--limit must be positive")

    recipe = RECIPE_BY_NAME.get(args.recipe) if args.recipe else None
    query = build_query(
        recipe=recipe,
        raw_query=args.query,
        extra_filter=args.extra_filter,
    )
    cards = search_cards(query, args.limit)
    if not cards:
        raise SystemExit(f"No cards found for query: {query}")

    if recipe is not None:
        print(f"Recipe: {recipe.name} -- {recipe.summary}")
    print(f"Query: {query}")
    for index, card in enumerate(cards, start=1):
        print()
        print(format_card(card, index))


if __name__ == "__main__":
    main()
