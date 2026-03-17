"""Draft history versioning and migration helpers."""

from pathlib import Path

CURRENT_DRAFT_HISTORY_VERSION = 2
_TOURNAMENTS_DIR = Path(__file__).resolve().parent.parent / "data" / "tournaments"


def _migrate_pick_v1_to_v2(pick: dict) -> None:
    """Convert a v1 pick with flat reasoning/thinking into v2 attempt records."""
    attempt: dict = {
        "attempt": 1,
        "response": pick["reasoning"],
        "accepted": True,
    }
    if pick.get("thinking"):
        attempt["thinking"] = pick["thinking"]
    pick["attempts"] = [attempt]
    pick.pop("thinking", None)


def migrate_draft_history_to_v2(draft: dict) -> dict:
    """Migrate a draft history dict from v1 to v2 in place."""
    version = draft.get("history_version", 1)
    assert version == 1, f"Expected v1 draft history, got v{version}"
    for pick in draft["picks"]:
        _migrate_pick_v1_to_v2(pick)
    draft["history_version"] = 2
    return draft


def migrate_draft_history_to_current(draft: dict) -> dict:
    """Migrate a draft history dict to the current version in place."""
    version = draft.get("history_version", 1)
    if version == CURRENT_DRAFT_HISTORY_VERSION:
        return draft
    if version == 1:
        return migrate_draft_history_to_v2(draft)
    raise AssertionError(f"Unsupported draft history version: v{version}")


def assert_current_draft_history_version(draft: dict) -> None:
    """Fail fast if the draft history is not at the current version."""
    version = draft.get("history_version", 1)
    assert version == CURRENT_DRAFT_HISTORY_VERSION, (
        f"Draft history is v{version}, expected v{CURRENT_DRAFT_HISTORY_VERSION}. "
        "Run `uv run python scripts/migrate_draft_histories.py --to 2`."
    )


def iter_tournament_paths() -> list[Path]:
    """Return all tournament JSON paths in the repo."""
    return sorted(_TOURNAMENTS_DIR.glob("season-*.json"))
