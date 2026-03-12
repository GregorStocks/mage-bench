"""Tests for draft history versioning and migrations."""

from scripts.draft_history import (
    CURRENT_DRAFT_HISTORY_VERSION,
    assert_current_draft_history_version,
    migrate_draft_history_to_current,
)


def test_migrate_v1_pick_to_v2_attempts() -> None:
    draft = {
        "packs_per_player": 4,
        "pool": ["Angels"],
        "picks": [
            {
                "seed": 1,
                "round": 1,
                "options": ["Angels"],
                "picked": "Angels",
                "reasoning": "**17**\n\nTake Angels.",
                "thinking": "Initial hidden reasoning",
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                "cost_usd": 0.01,
            }
        ],
        "decklists": {},
    }

    migrate_draft_history_to_current(draft)

    assert draft["history_version"] == CURRENT_DRAFT_HISTORY_VERSION
    pick = draft["picks"][0]
    assert "thinking" not in pick
    assert pick["attempts"] == [
        {
            "attempt": 1,
            "response": "**17**\n\nTake Angels.",
            "thinking": "Initial hidden reasoning",
            "accepted": True,
        }
    ]


def test_current_v2_history_is_unchanged() -> None:
    draft = {
        "history_version": CURRENT_DRAFT_HISTORY_VERSION,
        "packs_per_player": 4,
        "pool": ["Angels"],
        "picks": [
            {
                "seed": 1,
                "round": 1,
                "options": ["Angels"],
                "picked": "Angels",
                "reasoning": "17",
                "attempts": [
                    {
                        "attempt": 1,
                        "response": "17",
                        "accepted": True,
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                "cost_usd": 0.01,
            }
        ],
        "decklists": {},
    }

    migrated = migrate_draft_history_to_current(draft)

    assert migrated is draft
    assert_current_draft_history_version(draft)
