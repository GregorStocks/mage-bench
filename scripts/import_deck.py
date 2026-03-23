#!/usr/bin/env python3
"""Download a deck from MTGGoldfish and save it as a .dck file for XMage.

Uses Scryfall API to resolve card names to set codes and collector numbers.

Usage:
    import_deck.py <mtggoldfish-url> <output-file>

Example:
    import_deck.py https://www.mtggoldfish.com/deck/7616949 output.dck
"""

import re
import sys
from pathlib import Path

from magebench.common import http_utils
from magebench.game import scryfall

_MTGGOLDFISH_HOSTS = frozenset({"www.mtggoldfish.com"})


def download_deck_text(url: str) -> str:
    """Download plain text deck list from MTGGoldfish."""
    m = re.search(r"/deck/(\d+)", url)
    assert m, f"Could not extract deck ID from URL: {url}"
    deck_id = m.group(1)
    download_url = f"https://www.mtggoldfish.com/deck/download/{deck_id}"
    body = http_utils.fetch_https_bytes(
        download_url,
        allowed_hosts=_MTGGOLDFISH_HOSTS,
    )
    assert isinstance(body, bytes), f"MTGGoldfish returned non-bytes response: {type(body).__name__}"
    return body.decode("utf-8")


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


def resolve_cards(names: list[str]) -> dict[str, tuple[str, str]]:
    """Resolve card names to (set_code, collector_number) via Scryfall."""
    # Normalize split card names and track original -> normalized mapping
    norm_to_orig: dict[str, str] = {}
    normalized: list[str] = []
    for n in names:
        norm = _normalize_split_name(n)
        norm_to_orig[norm] = n
        normalized.append(norm)

    norm_resolved = scryfall.resolve_cards(normalized)

    # Map back to original names
    resolved: dict[str, tuple[str, str]] = {}
    for norm, val in norm_resolved.items():
        resolved[norm_to_orig.get(norm, norm)] = val

    missing = set(names) - set(resolved.keys())
    for name in sorted(missing):
        print(f"WARNING: card not found: {name}", file=sys.stderr)

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
