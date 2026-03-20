from __future__ import annotations

import pytest

from tests.golden_test_identities import (
    GoldenTestIdentity,
    get_golden_test_identity,
    golden_test,
    validate_golden_test_identities,
)


def test_golden_test_attaches_identity() -> None:
    @golden_test("initial_decision")
    def sample() -> None:
        pass

    identity = get_golden_test_identity(sample)
    assert identity == GoldenTestIdentity.from_case_id("initial_decision")


def test_identity_derives_distinct_usernames_and_labels() -> None:
    identity = GoldenTestIdentity.from_case_id("mana_drain_fact_or_fiction")

    assert identity.player_a_name != identity.player_b_name
    assert identity.player_a_name != identity.spectator_name
    assert identity.bridge_label != identity.opponent_label
    assert identity.bridge_label != identity.spectator_label
    assert len(identity.player_a_name) <= 14
    assert len(identity.player_b_name) <= 14
    assert len(identity.spectator_name) <= 14
    assert identity.player_a_name.replace("_", "").isalnum()


def test_validate_golden_test_identities_rejects_missing_identity() -> None:
    with pytest.raises(pytest.UsageError, match="missing @golden_test"):
        validate_golden_test_identities(
            [
                ("tests/test_golden_foo.py::test_foo", None),
            ]
        )


def test_validate_golden_test_identities_rejects_duplicate_case_ids() -> None:
    identity = GoldenTestIdentity.from_case_id("initial_decision")

    with pytest.raises(pytest.UsageError, match="Duplicate golden test identities"):
        validate_golden_test_identities(
            [
                ("tests/test_golden_a.py::test_a", identity),
                ("tests/test_golden_b.py::test_b", identity),
            ]
        )
