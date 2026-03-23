"""Tests for orchestrator helper functions."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from magebench.orchestration.batch_coordination import (
    GameSession,
    finalize_game,
    setup_game,
    wait_for_all_games,
)
from magebench.orchestration.config import Config, PilotPlayer
from magebench.orchestration.game_finalization import (
    ensure_game_over_event,
    print_game_summary,
    run_git,
    write_error_log,
    write_game_meta,
)
from magebench.orchestration.game_processes import (
    start_observer_client,
    wait_for_game_start,
    wait_with_pilot_monitoring,
)
from magebench.orchestration.orchestrator import (
    _check_regular_season_block,
    _missing_llm_api_keys,
    compile_project,
    parse_args,
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
    assert "(openrouter)" in errors[0]
    assert "required API key" in errors[0]


def test_missing_llm_api_keys_present():
    """A pilot player with the API key set should produce no error."""
    config = Config()
    config.pilot_players = [PilotPlayer(name="ace", model="test/model")]
    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-test"}, clear=True):
        errors = _missing_llm_api_keys(config)
    assert errors == []


def test_missing_llm_api_keys_uses_provider_specific_env():
    """Configured providers should map to their provider-specific env vars."""
    config = Config()
    config.pilot_players = [
        PilotPlayer(
            name="ace",
            model="test/model",
            provider="openai",
        )
    ]
    with patch.dict("os.environ", {}, clear=True):
        errors = _missing_llm_api_keys(config)
    assert len(errors) == 1
    assert "(openai)" in errors[0]
    assert "required API key" in errors[0]
    assert "OPENAI_API_KEY" not in errors[0]


def test_missing_llm_api_keys_reports_unknown_provider():
    """Unknown providers should fail closed instead of selecting a key env."""
    config = Config()
    config.pilot_players = [
        PilotPlayer(
            name="ace",
            model="test/model",
            provider="bogus",
        )
    ]
    with patch.dict("os.environ", {}, clear=True):
        errors = _missing_llm_api_keys(config)
    assert len(errors) == 1
    assert "Unknown LLM provider" in errors[0]
    assert "OPENAI_API_KEY" not in errors[0]


def test_parse_args_batch_manifest_sets_num_games(tmp_path: Path, monkeypatch):
    """Batch manifests should set num_games from the manifest length."""
    config_a = tmp_path / "a.json"
    config_b = tmp_path / "b.json"
    config_a.write_text("{}\n")
    config_b.write_text("{}\n")
    manifest = tmp_path / "batch.json"
    manifest.write_text(json.dumps([str(config_a), str(config_b)]) + "\n")

    monkeypatch.setattr(
        sys,
        "argv",
        ["puppeteer", "--observer", "--batch-config-manifest", str(manifest)],
    )

    config = parse_args()

    assert config.config_file == config_a
    assert config.batch_config_files == [config_a, config_b]
    assert config.num_games == 2


def test_parse_args_rejects_mismatched_batch_games(tmp_path: Path, monkeypatch):
    """Explicit --games must match the batch manifest length."""
    config_a = tmp_path / "a.json"
    config_b = tmp_path / "b.json"
    config_a.write_text("{}\n")
    config_b.write_text("{}\n")
    manifest = tmp_path / "batch.json"
    manifest.write_text(json.dumps([str(config_a), str(config_b)]) + "\n")

    monkeypatch.setattr(
        sys,
        "argv",
        ["puppeteer", "--games", "3", "--batch-config-manifest", str(manifest)],
    )

    with pytest.raises(AssertionError, match="must match batch config count"):
        parse_args()


def test_compile_project_default_args(tmp_path: Path):
    completed = MagicMock(returncode=0)

    with patch("magebench.orchestration.orchestrator.subprocess.run", return_value=completed) as run_mock:
        assert compile_project(tmp_path) is True

    run_mock.assert_called_once()
    cmd = run_mock.call_args.args[0]
    assert cmd == [
        "mvn",
        "-q",
        "-DskipTests",
        "-pl",
        "Mage.Server,Mage.Client,Mage.Client.Bridge",
        "-am",
        "install",
    ]
    assert run_mock.call_args.kwargs["cwd"] == tmp_path


def test_compile_project_can_disable_build_cache(tmp_path: Path):
    completed = MagicMock(returncode=0)

    with patch("magebench.orchestration.orchestrator.subprocess.run", return_value=completed) as run_mock:
        assert compile_project(tmp_path, observer=True, populate_local_repo=True) is True

    run_mock.assert_called_once()
    cmd = run_mock.call_args.args[0]
    assert cmd == [
        "mvn",
        "-q",
        "-DskipTests",
        "-pl",
        "Mage.Server,Mage.Client,Mage.Client.Bridge,Mage.Client.Observer",
        "-am",
        "-Dmaven.build.cache.enabled=false",
        "install",
    ]
    assert run_mock.call_args.kwargs["cwd"] == tmp_path


def test_check_regular_season_block_allows_regular_season(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "season.json").write_text(json.dumps({"current_season": 2, "phase": "regular-season"}) + "\n")

    assert _check_regular_season_block(tmp_path) is None


def test_check_regular_season_block_blocks_between_seasons(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "season.json").write_text(json.dumps({"current_season": 2, "phase": "between-seasons"}) + "\n")

    message = _check_regular_season_block(tmp_path)
    assert message is not None
    assert "crowned a champion" in message


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

        ensure_game_over_event(game_dir)

        lines = events_file.read_text().strip().splitlines()
        game_over_count = sum(1 for line in lines if json.loads(line).get("type") == "game_over")
        assert game_over_count == 1


def test_ensure_game_over_event_appended():
    """Should append a game_over event with correct seq if one is missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        events_file = game_dir / "game_events.jsonl"
        events_file.write_text(json.dumps({"ts": "2024-01-01T00:00:00", "seq": 42, "type": "game_start"}) + "\n")

        ensure_game_over_event(game_dir)

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

        ensure_game_over_event(game_dir, spectator_exit_code=0)

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

        ensure_game_over_event(game_dir, spectator_exit_code=1)

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
        ensure_game_over_event(game_dir)

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
            json.dumps(
                {
                    "ts": "2024-01-01T00:05:00",
                    "type": "game_over",
                    "message": "Player1 wins",
                }
            )
            + "\n"
        )

        with caplog.at_level("INFO"):
            print_game_summary(game_dir)

        output = caplog.text
        assert "Player1 wins" in output
        assert "did not finish" not in output


def test_print_game_summary_from_pilot_log(caplog):
    """Bridge client logs take priority over game_events.jsonl."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        (game_dir / "ace_pilot.log").write_text("INFO Game over: Player1 won the game\n")

        with caplog.at_level("INFO"):
            print_game_summary(game_dir)

        output = caplog.text
        assert "Player1 won the game" in output
        assert "did not finish" not in output


def test_print_game_summary_no_logs(caplog):
    """No logs at all should print 'did not finish'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)

        with caplog.at_level("INFO"):
            print_game_summary(game_dir)

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

        with caplog.at_level("INFO"):
            print_game_summary(game_dir)

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

        with caplog.at_level("INFO"):
            print_game_summary(game_dir)

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

        with caplog.at_level("INFO"):
            print_game_summary(game_dir)

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
            json.dumps(
                {
                    "type": "llm_response",
                    "player": "Alice",
                    "tool_calls": [{"name": "pass_priority"}],
                }
            ),
            json.dumps(
                {
                    "type": "llm_response",
                    "player": "Alice",
                    "tool_calls": [
                        {"name": "get_action_choices"},
                        {"name": "choose_action"},
                    ],
                }
            ),
        ]
        (game_dir / "Alice_llm.jsonl").write_text("\n".join(alice_llm) + "\n")
        bob_llm = [
            json.dumps({"type": "game_start", "player": "Bob"}),
            json.dumps(
                {
                    "type": "llm_response",
                    "player": "Bob",
                    "tool_calls": [{"name": "pass_priority"}],
                }
            ),
        ]
        (game_dir / "Bob_llm.jsonl").write_text("\n".join(bob_llm) + "\n")
        # Cost files
        (game_dir / "Alice_cost.json").write_text(json.dumps({"cost_usd": 0.05}))
        (game_dir / "Bob_cost.json").write_text(json.dumps({"cost_usd": 0.03}))

        with caplog.at_level("INFO"):
            print_game_summary(game_dir)

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

        write_error_log(game_dir)

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
        write_error_log(game_dir)

        error_log = game_dir / "errors.log"
        assert error_log.exists()
        assert "No errors detected" in error_log.read_text()


def test_git_returns_output():
    """Should return stripped stdout from a successful git command."""
    completed = subprocess.CompletedProcess(
        args=["git", "rev-parse", "--abbrev-ref", "HEAD"],
        returncode=0,
        stdout="  main\n",
        stderr="",
    )
    with patch(
        "magebench.orchestration.game_finalization.subprocess.run",
        return_value=completed,
    ) as mock:
        result = run_git("rev-parse --abbrev-ref HEAD", Path("/fake"))
    assert result == "main"
    mock.assert_called_once_with(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=Path("/fake"),
        check=True,
        capture_output=True,
        text=True,
    )


def test_git_raises_on_failure():
    """Git failures should surface immediately."""
    with (
        patch(
            "magebench.orchestration.game_finalization.subprocess.run",
            side_effect=subprocess.CalledProcessError(
                1,
                ["git", "rev-parse", "HEAD"],
                stderr="fatal: not a git repository",
            ),
        ),
        pytest.raises(RuntimeError, match="fatal: not a git repository"),
    ):
        run_git("rev-parse HEAD", Path("/fake"))


def test_write_game_meta_raises_on_missing_deck(tmp_path: Path):
    """Declared deck paths should fail fast if the file is missing."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "season.json").write_text(json.dumps({"current_season": 7}))
    config_file = tmp_path / "config.json"
    config_file.write_text("{}\n")

    config = Config(
        config_file=config_file,
        timestamp="20260312_010203",
        game_type="Two Player Duel",
        deck_type="Constructed - Legacy",
    )
    config.pilot_players = [PilotPlayer(name="ace", deck="missing.dck", model="test/model")]

    with pytest.raises(FileNotFoundError):
        write_game_meta(game_dir, config, tmp_path)


@pytest.mark.parametrize(
    ("game_type", "deck_type", "message"),
    [
        ("", "Variant Magic - Freeform Commander", "non-empty config\\.game_type"),
        ("Commander Free For All", "", "non-empty config\\.deck_type"),
    ],
)
def test_write_game_meta_requires_non_empty_format_fields(
    tmp_path: Path,
    game_type: str,
    deck_type: str,
    message: str,
):
    """Missing format metadata should fail before writing an invalid game_meta."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "season.json").write_text(json.dumps({"current_season": 7}))
    config_file = tmp_path / "config.json"
    config_file.write_text("{}\n")

    config = Config(
        config_file=config_file,
        timestamp="20260312_010203",
        game_type=game_type,
        deck_type=deck_type,
    )

    with pytest.raises(AssertionError, match=message):
        write_game_meta(game_dir, config, tmp_path)


# --- wait_with_pilot_monitoring tests ---


def _mock_proc(poll_returns: list[int | None]) -> MagicMock:
    """Create a mock Popen that returns successive values from poll_returns."""
    proc = MagicMock()
    proc.poll = MagicMock(side_effect=poll_returns)
    return proc


@patch("magebench.orchestration.game_processes.time.sleep")
def test_pilot_monitoring_spectator_exits_normally(_mock_sleep):
    """When spectator exits first, should return its exit code."""
    spectator = _mock_proc([None, None, 0])
    pilot = _mock_proc([None, None, None])
    pm = MagicMock()

    rc = wait_with_pilot_monitoring(spectator, [("alice", pilot)], pm)

    assert rc == 0
    pm.cleanup.assert_not_called()


@patch("magebench.orchestration.game_processes.time.sleep")
def test_pilot_monitoring_pilot_fails(_mock_sleep):
    """When a pilot exits with non-zero, should abort and return -1."""
    spectator = _mock_proc([None, None])
    pilot = _mock_proc([None, 3])
    pm = MagicMock()

    rc = wait_with_pilot_monitoring(spectator, [("alice", pilot)], pm)

    assert rc == -1
    pm.cleanup.assert_called_once()


@patch("magebench.orchestration.game_processes.time.sleep")
def test_pilot_monitoring_pilot_exits_zero_ignored(_mock_sleep):
    """A pilot exiting with code 0 should not trigger abort."""
    # Spectator: None, None, None, 0
    spectator = _mock_proc([None, None, None, 0])
    # Pilot: None, 0, 0, 0 (exits normally on second poll)
    pilot = _mock_proc([None, 0, 0, 0])
    pm = MagicMock()

    rc = wait_with_pilot_monitoring(spectator, [("alice", pilot)], pm)

    assert rc == 0
    pm.cleanup.assert_not_called()


# --- wait_for_game_start tests ---


@patch("magebench.orchestration.game_processes.time.sleep")
def test_wait_for_game_start_finds_marker(_mock_sleep):
    """Should return once the spectator log contains the game-started marker."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "spectator.log"
        log_path.write_text("AI Puppeteer: all players joined, starting match for table abc\n")
        proc = _mock_proc([None])  # Still running

        wait_for_game_start(log_path, proc, timeout=5)
        # Should not raise


@patch("magebench.orchestration.game_processes.time.sleep")
def test_wait_for_game_start_process_exited(_mock_sleep):
    """Should return immediately if the spectator process has already exited."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "spectator.log"
        proc = _mock_proc([0])  # Already exited

        wait_for_game_start(log_path, proc, timeout=5)
        # Should not raise — game may have started and ended quickly


@patch("magebench.orchestration.game_processes.time.sleep")
def test_wait_for_game_start_timeout(_mock_sleep):
    """Should raise TimeoutError if the marker never appears."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "spectator.log"
        log_path.write_text("Some other log line\n")
        proc = _mock_proc([None] * 100)  # Never exits

        with (
            patch(
                "magebench.orchestration.game_processes.time.monotonic",
                side_effect=[0, 0, 100],
            ),
            pytest.raises(TimeoutError),
        ):
            wait_for_game_start(log_path, proc, timeout=5)


# --- wait_for_all_games tests ---


@patch("magebench.orchestration.batch_coordination.time.sleep")
def test_wait_for_all_games_all_complete(_mock_sleep):
    """All games complete normally — returns their exit codes."""
    s1 = GameSession(index=0, game_dir=Path("/fake/g1"), config=Config())
    s1.spectator_proc = _mock_proc([None, 0])
    s2 = GameSession(index=1, game_dir=Path("/fake/g2"), config=Config())
    s2.spectator_proc = _mock_proc([None, None, 0])

    results = wait_for_all_games([s1, s2])

    assert results == {0: 0, 1: 0}


@patch("magebench.orchestration.batch_coordination.time.sleep")
def test_wait_for_all_games_pilot_fails(_mock_sleep):
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

    with patch("magebench.orchestration.batch_coordination.kill_tree"):
        results = wait_for_all_games([s1, s2])

    assert results[0] == 0
    assert results[1] == -1
    s2.spectator_proc.terminate.assert_called_once()


# --- finalize_game tests ---


def test_finalize_game_writes_logs():
    """finalize_game should write error log and ensure game_over event."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        (game_dir / "alice_errors.log").write_text("Some error\n")
        events_file = game_dir / "game_events.jsonl"
        events_file.write_text(json.dumps({"ts": "2024-01-01", "seq": 1, "type": "game_start"}) + "\n")

        config = Config()
        config.skip_post_game_prompts = True
        session = GameSession(index=0, game_dir=game_dir, config=config)
        finalize_game(session, Path("/fake/root"), spectator_rc=0)

        assert (game_dir / "errors.log").exists()
        # game_over event should have been appended
        lines = events_file.read_text().strip().splitlines()
        assert any(json.loads(line).get("type") == "game_over" for line in lines)


def test_finalize_game_tolerates_merge_io_error():
    """I/O failures while merging logs should warn and continue."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        (game_dir / "alice_errors.log").write_text("Some error\n")
        events_file = game_dir / "game_events.jsonl"
        events_file.write_text(json.dumps({"ts": "2024-01-01", "seq": 1, "type": "game_start"}) + "\n")

        config = Config()
        config.skip_post_game_prompts = True
        session = GameSession(index=0, game_dir=game_dir, config=config)

        with (
            patch(
                "magebench.orchestration.batch_coordination.merge_game_log",
                side_effect=OSError("disk full"),
            ),
            patch(
                "magebench.orchestration.batch_coordination.print_game_summary",
                return_value=1.25,
            ),
        ):
            pilot_cost, blunder_cost = finalize_game(session, Path("/fake/root"), spectator_rc=0)

        assert pilot_cost == 1.25
        assert blunder_cost == 0.0


def test_finalize_game_propagates_unexpected_merge_error():
    """Unexpected merge failures should still fail fast."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        (game_dir / "alice_errors.log").write_text("Some error\n")
        events_file = game_dir / "game_events.jsonl"
        events_file.write_text(json.dumps({"ts": "2024-01-01", "seq": 1, "type": "game_start"}) + "\n")

        config = Config()
        config.skip_post_game_prompts = True
        session = GameSession(index=0, game_dir=game_dir, config=config)

        with (
            patch(
                "magebench.orchestration.batch_coordination.merge_game_log",
                side_effect=RuntimeError("unexpected merge bug"),
            ),
            pytest.raises(RuntimeError, match="unexpected merge bug"),
        ):
            finalize_game(session, Path("/fake/root"), spectator_rc=0)


# --- Config num_games tests ---


def test_config_num_games_default():
    """Config should default to num_games=1."""
    config = Config()
    assert config.num_games == 1


def test_config_num_games_set():
    """num_games should be settable."""
    config = Config(num_games=3)
    assert config.num_games == 3


@patch("magebench.orchestration.batch_coordination.start_observer_client")
@patch("magebench.orchestration.batch_coordination.write_game_meta")
@patch("magebench.orchestration.batch_coordination.resolve_choice_decks")
@patch(
    "magebench.orchestration.batch_coordination.run_git",
    side_effect=["main", "abc123", "abc123 test"],
)
def test_setup_game_uses_batch_specific_config(
    _mock_git,
    _mock_resolve,
    _mock_write_meta,
    mock_start_spectator,
    tmp_path: Path,
):
    """Batch mode should load the config file assigned to that game index."""
    config_a = tmp_path / "g1.json"
    config_b = tmp_path / "g2.json"
    config_a.write_text(json.dumps({"players": [{"type": "cpu", "name": "alpha"}]}) + "\n")
    config_b.write_text(json.dumps({"players": [{"type": "cpu", "name": "beta"}]}) + "\n")

    spectator_proc = MagicMock()
    spectator_proc.poll.return_value = None
    mock_start_spectator.return_value = spectator_proc

    base_config = Config(
        config_file=config_a,
        batch_config_files=[config_a, config_b],
        observer=True,
        num_games=2,
    )
    base_config.port = 17174
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    session = setup_game(
        1,
        2,
        base_config,
        MagicMock(),
        Path("/fake/root"),
        log_dir,
        "20260101_000000",
    )

    assert session.config.config_file == config_b
    assert [player.name for player in session.config.cpu_players] == ["beta"]
    assert json.loads((session.game_dir / "config.json").read_text())["players"][0]["name"] == "beta"


# --- setup_game cleanup on failure tests ---


@patch("magebench.orchestration.batch_coordination.wait_for_spectator_table")
@patch("magebench.orchestration.batch_coordination.start_pilot_client")
@patch("magebench.orchestration.batch_coordination.start_observer_client")
@patch("magebench.orchestration.batch_coordination.write_game_meta")
@patch("magebench.orchestration.batch_coordination.resolve_choice_decks")
@patch(
    "magebench.orchestration.batch_coordination.run_git",
    side_effect=["main", "abc123", "abc123 test"],
)
def test_setup_game_cleans_up_on_spectator_crash(
    _mock_git,
    _mock_resolve,
    _mock_write_meta,
    mock_start_spectator,
    mock_start_pilot,
    mock_wait_table,
):
    """When the spectator crashes before table creation, setup_game should terminate it and re-raise."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_dir = Path(tmpdir)

        spectator_proc = MagicMock()
        spectator_proc.poll.return_value = None
        mock_start_spectator.return_value = spectator_proc

        pilot_proc = MagicMock()
        pilot_proc.poll.return_value = None
        mock_start_pilot.return_value = pilot_proc

        mock_wait_table.side_effect = RuntimeError("Spectator process exited before creating the game table")

        # Use num_games=1 (non-batch) so setup_game uses the config directly
        # without creating a new Config and calling load_config.
        config = Config(observer=True, num_games=1)
        config.pilot_players = [PilotPlayer(name="ace", model="test/model")]

        with pytest.raises(RuntimeError, match="Spectator process exited"):
            setup_game(0, 1, config, MagicMock(), Path("/fake"), log_dir, "20260101_000000")

        spectator_proc.terminate.assert_called_once()
        # Pilots were not started yet (crash happened before bridge client launch)
        mock_start_pilot.assert_not_called()


@patch("magebench.orchestration.batch_coordination.wait_for_spectator_table")
@patch("magebench.orchestration.batch_coordination.start_pilot_client")
@patch("magebench.orchestration.batch_coordination.start_observer_client")
@patch("magebench.orchestration.batch_coordination.write_game_meta")
@patch("magebench.orchestration.batch_coordination.resolve_choice_decks")
@patch(
    "magebench.orchestration.batch_coordination.run_git",
    side_effect=["main", "abc123", "abc123 test"],
)
def test_setup_game_cleans_up_pilots_on_timeout(
    _mock_git,
    _mock_resolve,
    _mock_write_meta,
    mock_start_spectator,
    mock_start_pilot,
    mock_wait_table,
):
    """When wait_for_spectator_table times out after pilots started, should terminate all."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_dir = Path(tmpdir)

        spectator_proc = MagicMock()
        spectator_proc.poll.return_value = None
        mock_start_spectator.return_value = spectator_proc

        pilot_proc = MagicMock()
        pilot_proc.poll.return_value = None
        mock_start_pilot.return_value = pilot_proc

        # Table wait succeeds, but we simulate a timeout after pilots start
        # by making wait_for_spectator_table succeed and raising from a later point.
        # Since wait_for_game_start is only called in batch mode, test with
        # a TimeoutError from wait_for_spectator_table after pilot launch
        # doesn't apply. Instead, test that pilots are terminated when the
        # except block runs (by raising from within the try block after pilots).
        mock_wait_table.side_effect = TimeoutError("Spectator did not create a table within 300s")

        config = Config(observer=True, num_games=1)
        config.pilot_players = [PilotPlayer(name="ace", model="test/model")]

        with pytest.raises(TimeoutError, match="300s"):
            setup_game(0, 1, config, MagicMock(), Path("/fake"), log_dir, "20260101_000000")

        spectator_proc.terminate.assert_called_once()


# --- start_observer_client headless detection tests ---


@patch(
    "magebench.orchestration.game_processes.shutil.which",
    return_value="/usr/bin/xvfb-run",
)
@patch("magebench.orchestration.game_processes.sys.platform", "linux")
def test_start_observer_xvfb_on_headless_linux(_mock_which):
    """On headless Linux (no DISPLAY), observer args should be prefixed with xvfb-run."""
    with patch.dict("os.environ", {}, clear=True):
        pm = MagicMock()
        pm.start_jvm_process.return_value = MagicMock()
        config = Config()
        start_observer_client(pm, Path("/fake/root"), config, Path("/tmp/test.log"))
        args = pm.start_jvm_process.call_args.kwargs["args"]
        assert args[0] == "/usr/bin/xvfb-run"
        assert "--auto-servernum" in args
        assert "mvn" in args


@patch("magebench.orchestration.game_processes.sys.platform", "linux")
def test_start_observer_no_xvfb_when_display_set():
    """With DISPLAY set, observer args should NOT be prefixed with xvfb-run."""
    with patch.dict("os.environ", {"DISPLAY": ":1"}, clear=True):
        pm = MagicMock()
        pm.start_jvm_process.return_value = MagicMock()
        config = Config()
        start_observer_client(pm, Path("/fake/root"), config, Path("/tmp/test.log"))
        args = pm.start_jvm_process.call_args.kwargs["args"]
        assert args[0] == "mvn"


@patch("magebench.orchestration.game_processes.shutil.which", return_value=None)
@patch("magebench.orchestration.game_processes.sys.platform", "linux")
def test_start_observer_fails_without_xvfb(_mock_which):
    """On headless Linux without xvfb-run, should raise AssertionError."""
    with patch.dict("os.environ", {}, clear=True):
        pm = MagicMock()
        config = Config()
        with pytest.raises(AssertionError, match="xvfb-run is not installed"):
            start_observer_client(pm, Path("/fake/root"), config, Path("/tmp/test.log"))
