from tests.golden_fail_fast import GoldenFailureGate


def test_skip_reason_requires_prior_failure() -> None:
    gate = GoldenFailureGate()

    assert gate.skip_reason_for("tests/test_golden_b.py::test_b", is_golden=True) is None
    assert gate.skip_reason_for("tests/test_golden_b.py::test_b", is_golden=False) is None


def test_skip_reason_targets_later_golden_tests() -> None:
    gate = GoldenFailureGate()
    gate.record_failure("tests/test_golden_a.py::test_a", "call")

    assert gate.skip_reason_for("tests/test_golden_a.py::test_a", is_golden=True) is None
    assert gate.skip_reason_for("tests/test_golden_b.py::test_b", is_golden=False) is None
    assert gate.skip_reason_for("tests/test_golden_b.py::test_b", is_golden=True) == (
        "Skipping after earlier golden failure in tests/test_golden_a.py::test_a "
        "(call) to avoid cascading follow-on failures from the shared golden harness."
    )


def test_first_failure_wins() -> None:
    gate = GoldenFailureGate()
    gate.record_failure("tests/test_golden_a.py::test_a", "setup")
    gate.record_failure("tests/test_golden_b.py::test_b", "teardown")

    assert gate.skip_reason_for("tests/test_golden_c.py::test_c", is_golden=True) == (
        "Skipping after earlier golden failure in tests/test_golden_a.py::test_a "
        "(setup) to avoid cascading follow-on failures from the shared golden harness."
    )
