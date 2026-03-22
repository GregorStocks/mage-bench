"""Helpers for parsing and extracting pilot game-state data."""

from magebench.game.decision_renderer import BASIC_LAND_NAMES


def extract_oracle_texts_from_board(board: list[dict]) -> dict[str, dict]:
    """Extract oracle text from board payload's rules fields.

    The bridge includes `rules` on every card (hand, battlefield, etc.).
    Convert these to the oracle_texts format expected by render_decision().
    """
    oracle_texts: dict[str, dict] = {}
    for player in board:
        for zone in ("hand", "battlefield", "graveyard", "exile", "commanders"):
            zone_cards = player.get(zone)
            if zone_cards is None:
                continue
            for card in zone_cards:
                if not isinstance(card, dict):
                    continue
                name = card["name"]
                if not name or name in BASIC_LAND_NAMES or name in oracle_texts:
                    continue
                rules = card.get("rules")
                if not rules:
                    continue
                entry: dict[str, str] = {}
                if card.get("mana_cost"):
                    entry["mana_cost"] = card["mana_cost"]
                if card.get("is_land"):
                    entry["type_line"] = "Land"
                if card.get("power") is not None:
                    entry["power_toughness"] = f"{card['power']}/{card['toughness']}"
                if rules:
                    entry["oracle_text"] = " / ".join(rules)
                oracle_texts[name] = entry
    return oracle_texts


def _normalize_context_token(raw: str | None) -> str | None:
    if raw is None:
        return None
    token = raw.strip()
    if not token:
        return None
    return token.upper().replace(" ", "_")


def parse_context_metadata(
    context: object,
) -> tuple[int | None, str | None, str | None, str | None]:
    """Parse bridge context strings like 'T3 Precombat Main/Precombat Main (Alice)'."""
    if context is None:
        return None, None, None, None
    assert isinstance(context, str), (
        f"context must be a string when present, got {context!r}"
    )

    parts = context.split(maxsplit=1)
    assert parts and parts[0].startswith("T"), (
        f"context must start with turn marker, got {context!r}"
    )
    turn = int(parts[0][1:])

    active_player: str | None = None
    phase_step = parts[1] if len(parts) > 1 else ""
    if phase_step and phase_step != "()":
        phase_prefix, sep, suffix = phase_step.partition(" (")
        if sep:
            player_name, closing, _ = suffix.partition(")")
            if closing:
                phase_step = phase_prefix
                active_player = player_name.strip() or None

    phase_raw, _, step_raw = phase_step.partition("/")
    phase = _normalize_context_token(phase_raw)
    step = _normalize_context_token(step_raw) or phase
    return turn, phase, step, active_player
