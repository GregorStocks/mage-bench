"""Unit tests for golden test timing instrumentation."""

import time

from tests.golden_helpers import (
    PhaseTiming,
    clear_timings,
    get_all_timings,
    print_timing_summary,
    timed_phase,
)


class TestTimedPhase:
    def setup_method(self):
        clear_timings()

    def test_records_timing(self):
        with timed_phase("test_foo", "compile"):
            time.sleep(0.01)

        timings = get_all_timings()
        assert len(timings) == 1
        assert timings[0].test_name == "test_foo"
        assert timings[0].phase == "compile"
        assert timings[0].duration >= 0.01

    def test_records_on_exception(self):
        """Timing is recorded even when the body raises."""
        try:
            with timed_phase("test_err", "boom"):
                time.sleep(0.01)
                raise ValueError("kaboom")
        except ValueError:
            pass

        timings = get_all_timings()
        assert len(timings) == 1
        assert timings[0].test_name == "test_err"
        assert timings[0].phase == "boom"
        assert timings[0].duration >= 0.01

    def test_multiple_phases(self):
        with timed_phase("session", "compilation"):
            pass
        with timed_phase("session", "server_startup"):
            pass
        with timed_phase("test_a", "spectator_startup"):
            pass
        with timed_phase("test_a", "replay"):
            pass

        timings = get_all_timings()
        assert len(timings) == 4
        assert [t.test_name for t in timings] == ["session", "session", "test_a", "test_a"]
        assert [t.phase for t in timings] == ["compilation", "server_startup", "spectator_startup", "replay"]

    def test_clear_timings(self):
        with timed_phase("test_x", "phase_y"):
            pass
        assert len(get_all_timings()) == 1
        clear_timings()
        assert len(get_all_timings()) == 0


class TestPrintTimingSummary:
    def setup_method(self):
        clear_timings()

    def test_empty_timings_no_output(self, capsys):
        print_timing_summary()
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_summary_includes_session_and_tests(self, capsys):
        # Inject mock timings directly
        from tests.golden_helpers import _all_timings

        _all_timings.extend(
            [
                PhaseTiming("session", "compilation", 30.0),
                PhaseTiming("session", "server_startup", 15.0),
                PhaseTiming("test_a", "spectator_startup", 5.0),
                PhaseTiming("test_a", "replay", 10.0),
                PhaseTiming("test_a", "golden_comparison", 0.5),
                PhaseTiming("test_b", "spectator_startup", 4.0),
                PhaseTiming("test_b", "replay", 8.0),
                PhaseTiming("test_b", "golden_comparison", 0.3),
            ]
        )

        print_timing_summary()
        output = capsys.readouterr().out

        assert "Golden Test Timing Summary" in output
        assert "Session setup:" in output
        assert "compilation" in output
        assert "server_startup" in output
        assert "Per-test breakdown:" in output
        assert "test_a" in output
        assert "test_b" in output
        assert "Aggregate" in output
        assert "replay" in output
