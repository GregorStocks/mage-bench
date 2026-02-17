#!/usr/bin/env python3
"""Download a deck from MTGGoldfish and save it as a .dck file for XMage.

Uses Scryfall API to resolve card names to set codes and collector numbers.

Usage:
    import-deck.py <mtggoldfish-url> <output-file>

Example:
    import-deck.py https://www.mtggoldfish.com/deck/7616949 output.dck
"""

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def download_deck_text(url: str) -> str:
    """Download plain text deck list from MTGGoldfish."""
    m = re.search(r"/deck/(\d+)", url)
    assert m, f"Could not extract deck ID from URL: {url}"
    deck_id = m.group(1)
    download_url = f"https://www.mtggoldfish.com/deck/download/{deck_id}"
    with urllib.request.urlopen(download_url) as resp:
        return resp.read().decode()


def parse_deck_text(deck_text: str) -> dict[str, list[tuple[int, bool]]]:
    """Parse deck text into {card_name: [(count, is_sideboard)]}."""
    cards: dict[str, list[tuple[int, bool]]] = {}
    sideboard = False
    for line in deck_text.strip().splitlines():
        line = line.strip()
        if not line:
            sideboard = True
            continue
        m = re.match(r"^(\d+)\s+(.+)$", line)
        if m:
            count, name = int(m.group(1)), m.group(2).strip()
            cards.setdefault(name, []).append((count, sideboard))
    return cards


def _normalize_split_name(name: str) -> str:
    """Normalize MTGGoldfish split/room card names for Scryfall.

    MTGGoldfish uses ``Wear/Tear`` but Scryfall expects ``Wear // Tear``.
    """
    if "/" in name and " // " not in name:
        return name.replace("/", " // ")
    return name


def _scryfall_collection(names: list[str]) -> tuple[list[dict], list[dict]]:
    """Query Scryfall ``/cards/collection`` for a batch of up to 75 names.

    Returns ``(found_cards, not_found_identifiers)``.
    """
    body = json.dumps({"identifiers": [{"name": n} for n in names]}).encode()
    req = urllib.request.Request(
        "https://api.scryfall.com/cards/collection",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return data.get("data", []), data.get("not_found", [])


def _scryfall_named(name: str) -> dict | None:
    """Look up a single card by exact name via ``/cards/named``."""
    qs = urllib.parse.urlencode({"exact": name})
    req = urllib.request.Request(f"https://api.scryfall.com/cards/named?{qs}")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError:
        return None


def resolve_cards(names: list[str]) -> dict[str, tuple[str, str]]:
    """Resolve card names to (set_code, collector_number) via Scryfall."""
    resolved: dict[str, tuple[str, str]] = {}

    # Normalize split card names and track original -> normalized mapping
    norm_to_orig: dict[str, str] = {}
    normalized: list[str] = []
    for n in names:
        norm = _normalize_split_name(n)
        norm_to_orig[norm] = n
        normalized.append(norm)

    # Primary resolution via /cards/collection
    not_found_norms: list[str] = []
    for i in range(0, len(normalized), 75):
        batch = normalized[i : i + 75]
        found, not_found = _scryfall_collection(batch)
        for card in found:
            orig = norm_to_orig.get(card["name"], card["name"])
            resolved[orig] = (card["set"].upper(), card["collector_number"])
        for nf in not_found:
            not_found_norms.append(nf["name"])

    # Fallback: for unresolved split cards, try the first half individually
    for nf_norm in not_found_norms:
        orig = norm_to_orig.get(nf_norm, nf_norm)
        if " // " in nf_norm:
            first_half = nf_norm.split(" // ")[0]
            card = _scryfall_named(first_half)
            if card is not None:
                resolved[orig] = (card["set"].upper(), card["collector_number"])
                continue
        print(f"WARNING: card not found: {orig}", file=sys.stderr)

    return resolved


def format_dck(
    cards: dict[str, list[tuple[int, bool]]],
    resolved: dict[str, tuple[str, str]],
) -> tuple[list[str], list[str]]:
    """Format cards as .dck lines. Returns (main_lines, sideboard_lines)."""
    main_lines: list[str] = []
    sb_lines: list[str] = []
    for name, entries in cards.items():
        if name not in resolved:
            continue
        set_code, num = resolved[name]
        for count, is_sb in entries:
            line = f"{count} [{set_code}:{num}] {name}"
            if is_sb:
                sb_lines.append(f"SB: {line}")
            else:
                main_lines.append(line)
    return main_lines, sb_lines


def main() -> None:
    assert len(sys.argv) == 3, f"Usage: {sys.argv[0]} <mtggoldfish-url> <output-file>"

    url = sys.argv[1]
    output = Path(sys.argv[2])

    deck_text = download_deck_text(url)
    cards = parse_deck_text(deck_text)
    resolved = resolve_cards(list(cards.keys()))
    main_lines, sb_lines = format_dck(cards, resolved)

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        for line in main_lines:
            f.write(line + "\n")
        for line in sb_lines:
            f.write(line + "\n")

    print(f"Saved {len(main_lines)} main / {len(sb_lines)} sideboard to {output}")


if __name__ == "__main__":
    main()
