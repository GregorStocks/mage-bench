"""Identity helpers for real golden integration tests.

Each golden test declares a stable case id once via ``@golden_test(...)``.
From that, we derive unique XMage usernames and process labels so multiple
golden tests can run against the same server without session-name conflicts.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypeVar

import pytest

_CASE_ATTR = "__golden_test_identity__"
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MAX_SLUG_LEN = 10
_HASH_LEN = 6
_ROLE_HASH_LEN = 4

_F = TypeVar("_F")


@dataclass(frozen=True)
class GoldenTestIdentity:
    """Per-test identity bundle for golden integration scenarios."""

    case_id: str
    slug: str
    player_a_name: str
    player_b_name: str
    spectator_name: str
    bridge_label: str
    opponent_label: str
    spectator_label: str

    @classmethod
    def from_case_id(cls, case_id: str) -> GoldenTestIdentity:
        normalized = _SLUG_RE.sub("-", case_id.strip().lower()).strip("-")
        assert normalized, "golden test case id must contain at least one alphanumeric character"
        digest = hashlib.blake2s(case_id.encode("utf-8"), digest_size=8).hexdigest()[:_HASH_LEN]
        slug = f"{normalized[:_MAX_SLUG_LEN]}-{digest}"
        role_hash = digest[:_ROLE_HASH_LEN]
        return cls(
            case_id=case_id,
            slug=slug,
            player_a_name=f"testp_{role_hash}",
            player_b_name=f"oppo_{role_hash}",
            spectator_name=f"spec_{role_hash}",
            bridge_label=f"{slug}-bridge",
            opponent_label=f"{slug}-opponent",
            spectator_label=f"{slug}-spectator",
        )

    def identifiers(self) -> dict[str, str]:
        """Return every value that must be unique across the golden suite."""
        return {
            "case_id": self.case_id,
            "player_a_name": self.player_a_name,
            "player_b_name": self.player_b_name,
            "spectator_name": self.spectator_name,
            "bridge_label": self.bridge_label,
            "opponent_label": self.opponent_label,
            "spectator_label": self.spectator_label,
        }

    def canonical_name_map(self) -> dict[str, str]:
        """Map runtime usernames back to stable golden-comparison names."""
        return {
            self.player_a_name: "TestPlayer",
            self.player_b_name: "Opponent",
        }


def golden_test(case_id: str):
    """Mark a real golden integration test and attach its identity metadata."""

    identity = GoldenTestIdentity.from_case_id(case_id)

    def decorator(func: _F) -> _F:
        setattr(func, _CASE_ATTR, identity)
        return pytest.mark.golden(func)

    return decorator


def get_golden_test_identity(test_obj: object) -> GoldenTestIdentity | None:
    """Return the attached golden test identity, if any."""
    return getattr(test_obj, _CASE_ATTR, None)


def validate_golden_test_identities(
    cases: Iterable[tuple[str, GoldenTestIdentity | None]],
) -> None:
    """Raise when a collected golden test is missing or duplicates an identity."""
    missing: list[str] = []
    seen: dict[str, tuple[str, str]] = {}
    duplicate_lines: list[str] = []

    for nodeid, identity in cases:
        if identity is None:
            missing.append(nodeid)
            continue
        for field, value in identity.identifiers().items():
            prior = seen.get(value)
            if prior is None:
                seen[value] = (nodeid, field)
                continue
            prior_nodeid, prior_field = prior
            duplicate_lines.append(f"{value!r}: {prior_nodeid} ({prior_field}) conflicts with {nodeid} ({field})")

    problems: list[str] = []
    if missing:
        problems.append("Golden tests missing @golden_test(...):\n  " + "\n  ".join(sorted(missing)))
    if duplicate_lines:
        problems.append("Duplicate golden test identities:\n  " + "\n  ".join(sorted(duplicate_lines)))
    if problems:
        raise pytest.UsageError("\n\n".join(problems))
