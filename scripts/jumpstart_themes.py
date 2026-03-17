"""Shared utilities for jumpstart theme scripts."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path


def collect_card_names(
    themes: dict[str, list[list[tuple[int, str]]]],
    basic_lands: dict[str, tuple[str, str]],
) -> list[str]:
    """Extract unique non-land card names from a themes dict."""
    names: set[str] = set()
    for variants in themes.values():
        for cards in variants:
            for _qty, name in cards:
                if name not in basic_lands:
                    names.add(name)
    return sorted(names)


def format_theme_entry(
    theme: str,
    variant_idx: int,
    num_variants: int,
    cards: list[tuple[int, str]],
    resolved: dict[str, tuple[str, str]],
    basic_lands: dict[str, tuple[str, str]],
) -> str:
    """Format a single theme variant in jumpstart.txt format."""
    header = f"# {theme}" if num_variants == 1 else f"# {theme} ({variant_idx + 1})"

    lines = [header]
    for qty, name in cards:
        if name in basic_lands:
            set_code, num = basic_lands[name]
        else:
            assert name in resolved, f"Card not resolved: {name}"
            set_code, num = resolved[name]
        lines.append(f"{qty} {set_code} {num} {name}")

    return "\n".join(lines)


def validate_themes(
    themes: dict[str, list[list[tuple[int, str]]]],
    resolved: dict[str, tuple[str, str]],
    basic_lands: dict[str, tuple[str, str]],
    expected_count: int = 20,
) -> None:
    """Assert all cards resolved and each variant has the expected card count."""
    missing = set(collect_card_names(themes, basic_lands)) - set(resolved.keys())
    assert not missing, f"Unresolved cards: {missing}"

    for theme, variants in themes.items():
        for i, cards in enumerate(variants):
            total = sum(qty for qty, _ in cards)
            assert total == expected_count, (
                f"{theme} variant {i + 1} has {total} cards, expected {expected_count}"
            )


def generate_and_append(
    themes: dict[str, list[list[tuple[int, str]]]],
    resolved: dict[str, tuple[str, str]],
    basic_lands: dict[str, tuple[str, str]],
    output_paths: Sequence[Path | str],
) -> None:
    """Generate jumpstart.txt entries and append to output files."""
    entries: list[str] = []
    for theme in sorted(themes.keys()):
        variants = themes[theme]
        for i, cards in enumerate(variants):
            entry = format_theme_entry(
                theme, i, len(variants), cards, resolved, basic_lands
            )
            entries.append(entry)

    text_to_append = "\n\n" + "\n\n".join(entries) + "\n"

    for path in output_paths:
        p = Path(path)
        with p.open("a") as f:
            f.write(text_to_append)
        print(f"Appended {len(entries)} theme variants to {p}")

    print(f"\nDone! Added {len(themes)} themes ({len(entries)} variants total)")
