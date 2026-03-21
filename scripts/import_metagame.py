#!/usr/bin/env python3
"""Scrape top decks from MTGGoldfish metagame pages and import them as .dck files.

Usage:
    import_metagame.py <format> [count]

Examples:
    import_metagame.py legacy        # Import top 20 Legacy decks
    import_metagame.py modern 15     # Import top 15 Modern decks
    import_metagame.py standard 20   # Import top 20 Standard decks
"""

import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
IMPORT_DECK = SCRIPT_DIR / "import_deck.py"
DECK_DIR = PROJECT_ROOT / "Mage.Client" / "release" / "sample-decks"

FORMATS = {
    "legacy": "Legacy",
    "modern": "Modern",
    "standard": "Standard",
}


def slug_to_title_case(slug: str) -> str:
    """Convert 'death-s-shadow' to 'Death-S-Shadow'."""
    return "-".join(word[0].upper() + word[1:] for word in slug.split("-"))


def clean_archetype_name(name: str) -> str:
    """Strip trailing UUIDs and numeric disambiguators from archetype slugs."""
    # e.g. "4c-reanimator-70c5fc5f-0149-4242-8b1c-dd0b72eeb297" -> "4c-reanimator"
    name = re.sub(r"-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$", "", name)
    # e.g. "death-s-shadow-472" -> "death-s-shadow"
    return re.sub(r"-\d+$", "", name)


def fetch_archetype_urls(fmt: str, count: int) -> list[str]:
    """Fetch archetype URLs from MTGGoldfish metagame page."""
    metagame_url = f"https://www.mtggoldfish.com/metagame/{fmt}/full#paper"
    with urllib.request.urlopen(metagame_url) as resp:
        html = resp.read().decode()

    pattern = f"/archetype/{fmt}-[a-z0-9-]+"
    all_matches = re.findall(pattern, html)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for match in all_matches:
        if match not in seen:
            seen.add(match)
            unique.append(match)

    return unique[:count]


def get_deck_id(archetype_url: str) -> str | None:
    """Get first deck ID from an archetype page."""
    with urllib.request.urlopen(archetype_url) as resp:
        html = resp.read().decode()
    m = re.search(r"/deck/(\d+)", html)
    return m.group(1) if m else None


def main() -> None:
    assert len(sys.argv) >= 2, (
        f"Usage: {sys.argv[0]} <format> [count]\n  format: legacy, modern, standard\n  count:  number of decks to import (default: 20)"
    )

    fmt = sys.argv[1]
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    assert fmt in FORMATS, f"Unknown format '{fmt}'. Use: {', '.join(FORMATS)}"

    output_dir = DECK_DIR / FORMATS[fmt]
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching {fmt} metagame from MTGGoldfish...")

    archetype_urls = fetch_archetype_urls(fmt, count)
    assert archetype_urls, f"No archetypes found for {fmt}"

    print("Found archetypes:")
    for url in archetype_urls:
        print(url)
    print()

    imported = 0
    for archetype_path in archetype_urls:
        archetype_name = archetype_path.replace(f"/archetype/{fmt}-", "")
        archetype_url = f"https://www.mtggoldfish.com{archetype_path}#paper"

        deck_id = get_deck_id(archetype_url)
        if not deck_id:
            print(f"WARNING: no deck found for {archetype_name}, skipping")
            continue

        clean_name = clean_archetype_name(archetype_name)
        filename = slug_to_title_case(clean_name)
        output_file = output_dir / f"{filename}.dck"

        if output_file.exists():
            print(f"SKIP: {output_file} already exists")
            imported += 1
            continue

        deck_url = f"https://www.mtggoldfish.com/deck/{deck_id}"
        print(f"Importing: {archetype_name} -> {output_file} (deck {deck_id})")

        result = subprocess.run(
            [sys.executable, str(IMPORT_DECK), deck_url, str(output_file)],
        )
        if result.returncode == 0:
            imported += 1
        else:
            print(f"WARNING: failed to import {archetype_name}")

        # Rate limit Scryfall API
        time.sleep(0.5)

    print()
    print(f"Done! Imported {imported}/{count} decks to {output_dir}")
    for dck in sorted(output_dir.glob("*.dck")):
        print(f"  {dck}")


if __name__ == "__main__":
    main()
