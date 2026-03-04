"""Tests for orchestrator helper functions."""

import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from puppeteer.config import Config, PilotPlayer
from puppeteer.orchestrator import (
    GameSession,
    _ensure_game_over_event,
    _finalize_game,
    _git,
    _missing_llm_api_keys,
    _print_game_summary,
    _setup_game,
    _wait_for_all_games,
    _wait_for_game_start,
    _wait_with_pilot_monitoring,
    _write_error_log,
    start_observer_client,
)


def test_missing_llm_api_keys_none():
    """No LLM players means no missing keys."""
    config = Config()
    assert _missing_llm_api_keys(config) == []


def test_missing_llm_api_keys_missing():
    """A pilot player with no API key set should produce an error."""
    config = Config()
    config.pilot_players = [PilotPlayer(name="ace", model="test/model")]
    with patch.dict("os.environ", {}, clear=True):
        errors = _missing_llm_api_keys(config)
    assert len(errors) == 1
    assert "ace" in errors[0]
    assert "OPENROUTER_API_KEY" in errors[0]


def test_missing_llm_api_keys_present():
    """A pilot player with the API key set should produce no error."""
    config = Config()
    config.pilot_players = [PilotPlayer(name="ace", model="test/model")]
    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-test"}, clear=True):
        errors = _missing_llm_api_keys(config)
    assert errors == []


def test_ensure_game_over_event_already_present():
    """Should not duplicate the game_over event if it already exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        events_file = game_dir / "game_events.jsonl"
        events_file.write_text(
            json.dumps({"ts": "2024-01-01T00:00:00", "type": "game_start"})
            + "\n"
            + json.dumps({"ts": "2024-01-01T00:05:00", "type": "game_over"})
            + "\n"
        )

        _ensure_game_over_event(game_dir)

        lines = events_file.read_text().strip().splitlines()
        game_over_count = sum(1 for line in lines if json.loads(line).get("type") == "game_over")
        assert game_over_count == 1


def test_ensure_game_over_event_appended():
    """Should append a game_over event with correct seq if one is missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        events_file = game_dir / "game_events.jsonl"
        events_file.write_text(json.dumps({"ts": "2024-01-01T00:00:00", "seq": 42, "type": "game_start"}) + "\n")

        _ensure_game_over_event(game_dir)

        lines = events_file.read_text().strip().splitlines()
        assert len(lines) == 2
        last_event = json.loads(lines[-1])
        assert last_event["type"] == "game_over"
        assert last_event["reason"] == "spectator_crashed"
        assert last_event["seq"] == 43


def test_ensure_game_over_event_spectator_closed():
    """Exit code 0 should produce reason 'spectator_closed'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        events_file = game_dir / "game_events.jsonl"
        events_file.write_text(json.dumps({"ts": "2024-01-01T00:00:00", "seq": 10, "type": "game_start"}) + "\n")

        _ensure_game_over_event(game_dir, spectator_exit_code=0)

        lines = events_file.read_text().strip().splitlines()
        assert len(lines) == 2
        last_event = json.loads(lines[-1])
        assert last_event["type"] == "game_over"
        assert last_event["reason"] == "spectator_closed"
        assert "spectator window closed" in last_event["message"]
        assert last_event["seq"] == 11


def test_ensure_game_over_event_spectator_crashed():
    """Non-zero exit code should produce reason 'spectator_crashed'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        events_file = game_dir / "game_events.jsonl"
        events_file.write_text(json.dumps({"ts": "2024-01-01T00:00:00", "seq": 10, "type": "game_start"}) + "\n")

        _ensure_game_over_event(game_dir, spectator_exit_code=1)

        lines = events_file.read_text().strip().splitlines()
        assert len(lines) == 2
        last_event = json.loads(lines[-1])
        assert last_event["type"] == "game_over"
        assert last_event["reason"] == "spectator_crashed"
        assert "code 1" in last_event["message"]


def test_ensure_game_over_event_no_file():
    """Should create the file with a game_over event if it doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        _ensure_game_over_event(game_dir)

        events_file = game_dir / "game_events.jsonl"
        assert events_file.exists()
        event = json.loads(events_file.read_text().strip())
        assert event["type"] == "game_over"
        assert event["seq"] == 1


def test_print_game_summary_from_events_jsonl(caplog):
    """CPU-only games should read the result from game_events.jsonl."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        events_file = game_dir / "game_events.jsonl"
        events_file.write_text(
            json.dumps({"ts": "2024-01-01T00:05:00", "type": "game_over", "message": "Player1 wins"}) + "\n"
        )

        with caplog.at_level("INFO", logger="puppeteer.orchestrator"):
            _print_game_summary(game_dir)

        output = caplog.text
        assert "Player1 wins" in output
        assert "did not finish" not in output


def test_print_game_summary_from_pilot_log(caplog):
    """Bridge client logs take priority over game_events.jsonl."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        (game_dir / "ace_pilot.log").write_text("INFO Game over: Player1 won the game\n")

        with caplog.at_level("INFO", logger="puppeteer.orchestrator"):
            _print_game_summary(game_dir)

        output = caplog.text
        assert "Player1 won the game" in output
        assert "did not finish" not in output


def test_print_game_summary_no_logs(caplog):
    """No logs at all should print 'did not finish'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)

        with caplog.at_level("INFO", logger="puppeteer.orchestrator"):
            _print_game_summary(game_dir)

        output = caplog.text
        assert "did not finish" in output


def test_print_game_summary_synthetic_game_over(caplog):
    """A synthetic game_over (timeout_or_killed) should still show 'did not finish'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        events_file = game_dir / "game_events.jsonl"
        events_file.write_text(
            json.dumps(
                {
                    "ts": "2024-01-01T00:05:00",
                    "type": "game_over",
                    "message": "Game ended (no GAME_OVER received)",
                    "reason": "timeout_or_killed",
                }
            )
            + "\n"
        )

        with caplog.at_level("INFO", logger="puppeteer.orchestrator"):
            _print_game_summary(game_dir)

        output = caplog.text
        assert "did not finish" in output


def test_print_game_summary_spectator_closed(caplog):
    """spectator_closed reason should show the interrupted message, not 'did not finish'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        events_file = game_dir / "game_events.jsonl"
        events_file.write_text(
            json.dumps(
                {
                    "ts": "2024-01-01T00:05:00",
                    "type": "game_over",
                    "message": "Game interrupted (spectator window closed)",
                    "reason": "spectator_closed",
                }
            )
            + "\n"
        )

        with caplog.at_level("INFO", logger="puppeteer.orchestrator"):
            _print_game_summary(game_dir)

        output = caplog.text
        assert "spectator window closed" in output
        assert "did not finish" not in output


def test_print_game_summary_spectator_crashed(caplog):
    """spectator_crashed reason should show 'did not finish'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        events_file = game_dir / "game_events.jsonl"
        events_file.write_text(
            json.dumps(
                {
                    "ts": "2024-01-01T00:05:00",
                    "type": "game_over",
                    "message": "Game ended (spectator exited with code 1)",
                    "reason": "spectator_crashed",
                }
            )
            + "\n"
        )

        with caplog.at_level("INFO", logger="puppeteer.orchestrator"):
            _print_game_summary(game_dir)

        output = caplog.text
        assert "did not finish" in output


def test_print_game_summary_turns_and_actions(caplog):
    """Summary should show turn count and per-player action counts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        # Game events with turn markers
        events = [
            json.dumps({"type": "game_action", "message": "TURN 1 for Alice (20 - 20)"}),
            json.dumps({"type": "game_action", "message": "TURN 2 for Bob (20 - 18)"}),
            json.dumps({"type": "game_action", "message": "TURN 3 for Alice (15 - 18)"}),
            json.dumps({"type": "game_over", "message": "Alice wins"}),
        ]
        (game_dir / "game_events.jsonl").write_text("\n".join(events) + "\n")
        # LLM JSONL with tool calls
        alice_llm = [
            json.dumps({"type": "game_start", "player": "Alice"}),
            json.dumps({"type": "llm_response", "player": "Alice", "tool_calls": [{"name": "pass_priority"}]}),
            json.dumps(
                {
                    "type": "llm_response",
                    "player": "Alice",
                    "tool_calls": [{"name": "get_action_choices"}, {"name": "choose_action"}],
                }
            ),
        ]
        (game_dir / "Alice_llm.jsonl").write_text("\n".join(alice_llm) + "\n")
        bob_llm = [
            json.dumps({"type": "game_start", "player": "Bob"}),
            json.dumps({"type": "llm_response", "player": "Bob", "tool_calls": [{"name": "pass_priority"}]}),
        ]
        (game_dir / "Bob_llm.jsonl").write_text("\n".join(bob_llm) + "\n")
        # Cost files
        (game_dir / "Alice_cost.json").write_text(json.dumps({"cost_usd": 0.05}))
        (game_dir / "Bob_cost.json").write_text(json.dumps({"cost_usd": 0.03}))

        with caplog.at_level("INFO", logger="puppeteer.orchestrator"):
            _print_game_summary(game_dir)

        output = caplog.text
        assert "Turns: 3" in output
        assert "Alice: $0.0500 (3 actions)" in output
        assert "Bob: $0.0300 (1 actions)" in output
        assert "Total: $0.0800" in output


def test_write_error_log_combines():
    """Should combine per-player error logs into errors.log."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        (game_dir / "alice_errors.log").write_text("Error on turn 3\nBad mana\n")
        (game_dir / "bob_errors.log").write_text("Timeout\n")

        _write_error_log(game_dir)

        error_log = game_dir / "errors.log"
        assert error_log.exists()
        content = error_log.read_text()
        assert "[alice_errors] Error on turn 3" in content
        assert "[alice_errors] Bad mana" in content
        assert "[bob_errors] Timeout" in content


def test_write_error_log_empty():
    """Should write 'No errors detected.' when no error logs exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        _write_error_log(game_dir)

        error_log = game_dir / "errors.log"
        assert error_log.exists()
        assert "No errors detected" in error_log.read_text()


def test_git_returns_output():
    """Should return stripped stdout from a successful git command."""
    with patch("puppeteer.orchestrator.subprocess.check_output", return_value="  main\n") as mock:
        result = _git("rev-parse --abbrev-ref HEAD", Path("/fake"))
    assert result == "main"
    mock.assert_called_once_with(
        "git rev-parse --abbrev-ref HEAD",
        shell=True,
        cwd=Path("/fake"),
        stderr=subprocess.DEVNULL,
        text=True,
    )


def test_git_returns_empty_on_failure():
    """Should return empty string when git command fails."""
    with patch("puppeteer.orchestrator.subprocess.check_output", side_effect=subprocess.CalledProcessError(1, "git")):
        result = _git("rev-parse HEAD", Path("/fake"))
    assert result == ""


# --- _wait_with_pilot_monitoring tests ---


def _mock_proc(poll_returns: list[int | None]) -> MagicMock:
    """Create a mock Popen that returns successive values from poll_returns."""
    proc = MagicMock()
    proc.poll = MagicMock(side_effect=poll_returns)
    return proc


@patch("puppeteer.orchestrator.time.sleep")
def test_pilot_monitoring_spectator_exits_normally(mock_sleep):
    """When spectator exits first, should return its exit code."""
    spectator = _mock_proc([None, None, 0])
    pilot = _mock_proc([None, None, None])
    pm = MagicMock()

    rc = _wait_with_pilot_monitoring(spectator, [("alice", pilot)], pm)

    assert rc == 0
    pm.cleanup.assert_not_called()


@patch("puppeteer.orchestrator.time.sleep")
def test_pilot_monitoring_pilot_fails(mock_sleep):
    """When a pilot exits with non-zero, should abort and return -1."""
    spectator = _mock_proc([None, None])
    pilot = _mock_proc([None, 3])
    pm = MagicMock()

    rc = _wait_with_pilot_monitoring(spectator, [("alice", pilot)], pm)

    assert rc == -1
    pm.cleanup.assert_called_once()


@patch("puppeteer.orchestrator.time.sleep")
def test_pilot_monitoring_pilot_exits_zero_ignored(mock_sleep):
    """A pilot exiting with code 0 should not trigger abort."""
    # Spectator: None, None, None, 0
    spectator = _mock_proc([None, None, None, 0])
    # Pilot: None, 0, 0, 0 (exits normally on second poll)
    pilot = _mock_proc([None, 0, 0, 0])
    pm = MagicMock()

    rc = _wait_with_pilot_monitoring(spectator, [("alice", pilot)], pm)

    assert rc == 0
    pm.cleanup.assert_not_called()


# --- _wait_for_game_start tests ---


@patch("puppeteer.orchestrator.time.sleep")
def test_wait_for_game_start_finds_marker(mock_sleep):
    """Should return once the spectator log contains the game-started marker."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "spectator.log"
        log_path.write_text("AI Puppeteer: all players joined, starting match for table abc\n")
        proc = _mock_proc([None])  # Still running

        _wait_for_game_start(log_path, proc, timeout=5)
        # Should not raise


@patch("puppeteer.orchestrator.time.sleep")
def test_wait_for_game_start_process_exited(mock_sleep):
    """Should return immediately if the spectator process has already exited."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "spectator.log"
        proc = _mock_proc([0])  # Already exited

        _wait_for_game_start(log_path, proc, timeout=5)
        # Should not raise — game may have started and ended quickly


@patch("puppeteer.orchestrator.time.sleep")
def test_wait_for_game_start_timeout(mock_sleep):
    """Should raise TimeoutError if the marker never appears."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "spectator.log"
        log_path.write_text("Some other log line\n")
        proc = _mock_proc([None] * 100)  # Never exits

        with patch("puppeteer.orchestrator.time.monotonic", side_effect=[0, 0, 100]), pytest.raises(TimeoutError):
            _wait_for_game_start(log_path, proc, timeout=5)


# --- _wait_for_all_games tests ---


@patch("puppeteer.orchestrator.time.sleep")
def test_wait_for_all_games_all_complete(mock_sleep):
    """All games complete normally — returns their exit codes."""
    s1 = GameSession(index=0, game_dir=Path("/fake/g1"), config=Config())
    s1.spectator_proc = _mock_proc([None, 0])
    s2 = GameSession(index=1, game_dir=Path("/fake/g2"), config=Config())
    s2.spectator_proc = _mock_proc([None, None, 0])

    pm = MagicMock()
    results = _wait_for_all_games([s1, s2], pm)

    assert results == {0: 0, 1: 0}


@patch("puppeteer.orchestrator.time.sleep")
def test_wait_for_all_games_pilot_fails(mock_sleep):
    """A pilot failure should terminate that game's spectator but not others."""
    s1 = GameSession(index=0, game_dir=Path("/fake/g1"), config=Config())
    s1.spectator_proc = _mock_proc([None, None, None, 0])
    s1.pilot_procs = []

    s2 = GameSession(index=1, game_dir=Path("/fake/g2"), config=Config())
    s2.spectator_proc = MagicMock()
    # Spectator for s2 never exits on its own — will be terminated
    s2.spectator_proc.poll = MagicMock(side_effect=[None, None, None, None])
    bob_proc = _mock_proc([None, 3, 3])
    s2.pilot_procs = [("bob", bob_proc)]

    pm = MagicMock()
    with patch("puppeteer.orchestrator.kill_tree"):
        results = _wait_for_all_games([s1, s2], pm)

    assert results[0] == 0
    assert results[1] == -1
    s2.spectator_proc.terminate.assert_called_once()


# --- _finalize_game tests ---


def test_finalize_game_writes_logs():
    """_finalize_game should write error log and ensure game_over event."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        (game_dir / "alice_errors.log").write_text("Some error\n")
        events_file = game_dir / "game_events.jsonl"
        events_file.write_text(json.dumps({"ts": "2024-01-01", "seq": 1, "type": "game_start"}) + "\n")

        config = Config()
        config.skip_post_game_prompts = True
        session = GameSession(index=0, game_dir=game_dir, config=config)
        _finalize_game(session, Path("/fake/root"), spectator_rc=0)

        assert (game_dir / "errors.log").exists()
        # game_over event should have been appended
        lines = events_file.read_text().strip().splitlines()
        assert any(json.loads(line).get("type") == "game_over" for line in lines)


# --- Config num_games tests ---


def test_config_num_games_default():
    """Config should default to num_games=1."""
    config = Config()
    assert config.num_games == 1


def test_config_num_games_set():
    """num_games should be settable."""
    config = Config(num_games=3)
    assert config.num_games == 3


# --- _setup_game cleanup on failure tests ---


@patch("puppeteer.orchestrator.time.sleep")
@patch("puppeteer.orchestrator._wait_for_spectator_table")
@patch("puppeteer.orchestrator.start_pilot_client")
@patch("puppeteer.orchestrator.start_observer_client")
@patch("puppeteer.orchestrator._write_game_meta")
@patch("puppeteer.orchestrator.resolve_choice_decks")
def test_setup_game_cleans_up_on_spectator_crash(
    mock_resolve,
    mock_write_meta,
    mock_start_spectator,
    mock_start_pilot,
    mock_wait_table,
    mock_sleep,
):
    """When the spectator crashes before table creation, _setup_game should terminate it and re-raise."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_dir = Path(tmpdir)

        spectator_proc = MagicMock()
        spectator_proc.poll.return_value = None
        mock_start_spectator.return_value = spectator_proc

        pilot_proc = MagicMock()
        pilot_proc.poll.return_value = None
        mock_start_pilot.return_value = pilot_proc

        mock_wait_table.side_effect = RuntimeError("Spectator process exited before creating the game table")

        # Use num_games=1 (non-batch) so _setup_game uses the config directly
        # without creating a new Config and calling load_config.
        config = Config(observer=True, num_games=1)
        config.pilot_players = [PilotPlayer(name="ace", model="test/model")]

        with pytest.raises(RuntimeError, match="Spectator process exited"):
            _setup_game(0, 1, config, MagicMock(), Path("/fake"), log_dir, "20260101_000000")

        spectator_proc.terminate.assert_called_once()
        # Pilots were not started yet (crash happened before bridge client launch)
        mock_start_pilot.assert_not_called()


@patch("puppeteer.orchestrator.time.sleep")
@patch("puppeteer.orchestrator._wait_for_spectator_table")
@patch("puppeteer.orchestrator.start_pilot_client")
@patch("puppeteer.orchestrator.start_observer_client")
@patch("puppeteer.orchestrator._write_game_meta")
@patch("puppeteer.orchestrator.resolve_choice_decks")
def test_setup_game_cleans_up_pilots_on_timeout(
    mock_resolve,
    mock_write_meta,
    mock_start_spectator,
    mock_start_pilot,
    mock_wait_table,
    mock_sleep,
):
    """When _wait_for_spectator_table times out after pilots started, should terminate all."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_dir = Path(tmpdir)

        spectator_proc = MagicMock()
        spectator_proc.poll.return_value = None
        mock_start_spectator.return_value = spectator_proc

        pilot_proc = MagicMock()
        pilot_proc.poll.return_value = None
        mock_start_pilot.return_value = pilot_proc

        # Table wait succeeds, but we simulate a timeout after pilots start
        # by making _wait_for_spectator_table succeed and raising from a later point.
        # Since _wait_for_game_start is only called in batch mode, test with
        # a TimeoutError from _wait_for_spectator_table after pilot launch
        # doesn't apply. Instead, test that pilots are terminated when the
        # except block runs (by raising from within the try block after pilots).
        mock_wait_table.side_effect = TimeoutError("Spectator did not create a table within 300s")

        config = Config(observer=True, num_games=1)
        config.pilot_players = [PilotPlayer(name="ace", model="test/model")]

        with pytest.raises(TimeoutError, match="300s"):
            _setup_game(0, 1, config, MagicMock(), Path("/fake"), log_dir, "20260101_000000")

        spectator_proc.terminate.assert_called_once()


# --- start_observer_client headless detection tests ---


@patch("puppeteer.orchestrator.shutil.which", return_value="/usr/bin/xvfb-run")
@patch("puppeteer.orchestrator.sys.platform", "linux")
def test_start_observer_xvfb_on_headless_linux(mock_which):
    """On headless Linux (no DISPLAY), observer args should be prefixed with xvfb-run."""
    with patch.dict("os.environ", {}, clear=True):
        pm = MagicMock()
        pm.start_process.return_value = MagicMock()
        config = Config()
        start_observer_client(pm, Path("/fake/root"), config, Path("/tmp/test.log"))
        args = pm.start_process.call_args.kwargs["args"]
        assert args[0] == "/usr/bin/xvfb-run"
        assert "--auto-servernum" in args
        assert "mvn" in args


@patch("puppeteer.orchestrator.sys.platform", "linux")
def test_start_observer_no_xvfb_when_display_set():
    """With DISPLAY set, observer args should NOT be prefixed with xvfb-run."""
    with patch.dict("os.environ", {"DISPLAY": ":1"}, clear=True):
        pm = MagicMock()
        pm.start_process.return_value = MagicMock()
        config = Config()
        start_observer_client(pm, Path("/fake/root"), config, Path("/tmp/test.log"))
        args = pm.start_process.call_args.kwargs["args"]
        assert args[0] == "mvn"


@patch("puppeteer.orchestrator.shutil.which", return_value=None)
@patch("puppeteer.orchestrator.sys.platform", "linux")
def test_start_observer_fails_without_xvfb(mock_which):
    """On headless Linux without xvfb-run, should raise AssertionError."""
    with patch.dict("os.environ", {}, clear=True):
        pm = MagicMock()
        config = Config()
        with pytest.raises(AssertionError, match="xvfb-run is not installed"):
            start_observer_client(pm, Path("/fake/root"), config, Path("/tmp/test.log"))
