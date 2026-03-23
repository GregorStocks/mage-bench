"""Tests for game_log: JSONL writer, decklist parser, log merging."""

import json
import tempfile
from pathlib import Path

import pytest

from magebench.game.game_log import GameLogWriter, _parse_ts, merge_game_log, read_decklist


def test_read_decklist():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".dck", delete=False) as f:
        f.write("# Main deck\n")
        f.write("// Comment line\n")
        f.write("\n")
        f.write("4 [LCI:123] Lightning Bolt\n")
        f.write("2 [M21:456] Opt\n")
        f.write("SB: 1 [ONE:789] Swords to Plowshares\n")
        path = Path(f.name)

    try:
        entries = read_decklist(path)
        assert len(entries) == 3
        assert entries[0] == "4 [LCI:123] Lightning Bolt"
        assert entries[1] == "2 [M21:456] Opt"
        assert entries[2] == "SB: 1 [ONE:789] Swords to Plowshares"
    finally:
        path.unlink()


def test_read_decklist_missing_file():
    with pytest.raises(FileNotFoundError):
        read_decklist(Path("/nonexistent/deck.dck"))


def test_parse_ts_iso():
    ts = _parse_ts("2024-06-15T10:30:00-07:00")
    assert ts is not None
    assert isinstance(ts, float)


def test_parse_ts_with_z():
    ts = _parse_ts("2024-06-15T17:30:00.123Z")
    assert ts is not None
    assert isinstance(ts, float)


def test_parse_ts_naive():
    ts = _parse_ts("2024-06-15T17:30:00")
    assert ts is not None


def test_parse_ts_invalid():
    with pytest.raises(ValueError, match="not-a-timestamp"):
        _parse_ts("not-a-timestamp")


def test_parse_ts_empty():
    assert _parse_ts("") is None


def test_merge_game_log_invalid_timestamp_raises():
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        events = game_dir / "game_events.jsonl"
        events.write_text(json.dumps({"ts": "not-a-timestamp", "type": "game_start"}) + "\n")

        with pytest.raises(ValueError, match="not-a-timestamp"):
            merge_game_log(game_dir)


def test_game_log_writer_emit():
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        with GameLogWriter(game_dir, "alice") as writer:
            writer.emit("turn_start", turn=1)
            writer.emit("action", action="play_land")

        log_path = game_dir / "alice_llm.jsonl"
        assert log_path.exists()
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 2

        event1 = json.loads(lines[0])
        assert event1["seq"] == 1
        assert event1["type"] == "turn_start"
        assert event1["player"] == "alice"
        assert event1["turn"] == 1
        assert "ts" in event1

        event2 = json.loads(lines[1])
        assert event2["seq"] == 2
        assert event2["type"] == "action"


def test_game_log_writer_cost_tracking():
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        with GameLogWriter(game_dir, "bob") as writer:
            assert writer.last_cumulative_cost_usd() == 0.0

            writer.emit("llm_call", cumulative_cost_usd=0.05)
            assert writer.last_cumulative_cost_usd() == 0.05

            writer.emit("llm_call", cumulative_cost_usd=0.12)
            assert writer.last_cumulative_cost_usd() == 0.12

            with pytest.raises(
                AssertionError,
                match="cumulative_cost_usd must parse as a float",
            ):
                writer.emit("llm_call", cumulative_cost_usd="bad")


def test_game_log_writer_custom_suffix():
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        with GameLogWriter(game_dir, "alice", suffix="llm_trace") as writer:
            writer.emit(
                "llm_call",
                request={
                    "model": "test",
                    "messages": [{"role": "user", "content": "hi"}],
                },
                response={"choices": [{"message": {"content": "hello"}}]},
            )

        log_path = game_dir / "alice_llm_trace.jsonl"
        assert log_path.exists()
        # Default suffix file should NOT exist
        assert not (game_dir / "alice_llm.jsonl").exists()

        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["type"] == "llm_call"
        assert event["request"]["model"] == "test"
        assert event["response"]["choices"][0]["message"]["content"] == "hello"


def test_game_log_writer_context_manager_closes_on_exception():
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        try:
            with GameLogWriter(game_dir, "alice") as writer:
                writer.emit("turn_start", turn=1)
                raise RuntimeError("simulated crash")
        except RuntimeError:
            pass

        # File should be closed and flushed despite the exception
        log_path = game_dir / "alice_llm.jsonl"
        assert log_path.exists()
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["type"] == "turn_start"


def test_merge_game_log():
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)

        # Write game_events.jsonl with two events
        events = game_dir / "game_events.jsonl"
        events.write_text(
            json.dumps({"ts": "2024-06-15T10:00:01.000-07:00", "type": "game_start"})
            + "\n"
            + json.dumps({"ts": "2024-06-15T10:00:03.000-07:00", "type": "game_over"})
            + "\n"
        )

        # Write a player LLM log with an event between the two game events
        llm_log = game_dir / "alice_llm.jsonl"
        llm_log.write_text(
            json.dumps(
                {
                    "ts": "2024-06-15T10:00:02.000-07:00",
                    "type": "llm_call",
                    "player": "alice",
                }
            )
            + "\n"
        )

        merge_game_log(game_dir)

        merged = game_dir / "game.jsonl"
        assert merged.exists()
        lines = merged.read_text().strip().splitlines()
        assert len(lines) == 3

        types = [json.loads(line)["type"] for line in lines]
        assert types == ["game_start", "llm_call", "game_over"]


def test_merge_game_seq_ordering():
    """Events with game_seq should sort by game_seq, not timestamp."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)

        # Server events use seq as their game_seq
        server = game_dir / "server_game_events.jsonl"
        server.write_text(
            json.dumps({"seq": 5, "type": "game_action", "message": "second"})
            + "\n"
            + json.dumps({"seq": 3, "type": "game_action", "message": "first"})
            + "\n"
        )

        # Observer events with game_seq should interleave
        events = game_dir / "game_events.jsonl"
        events.write_text(
            json.dumps(
                {
                    "ts": "2024-06-15T10:00:01.000-07:00",
                    "game_seq": 4,
                    "type": "state_snapshot",
                }
            )
            + "\n"
        )

        # LLM events without game_seq sort after
        llm = game_dir / "alice_llm.jsonl"
        llm.write_text(
            json.dumps(
                {
                    "ts": "2024-06-15T10:00:00.000-07:00",
                    "type": "llm_call",
                    "player": "alice",
                }
            )
            + "\n"
        )

        merge_game_log(game_dir)

        merged = game_dir / "game.jsonl"
        lines = merged.read_text().strip().splitlines()
        assert len(lines) == 4

        events_parsed = [json.loads(line) for line in lines]
        # game_seq 3, 4, 5 first, then llm_call (no game_seq) last
        assert events_parsed[0]["message"] == "first"  # seq 3
        assert events_parsed[1]["type"] == "state_snapshot"  # game_seq 4
        assert events_parsed[2]["message"] == "second"  # seq 5
        assert events_parsed[3]["type"] == "llm_call"  # no game_seq


def test_merge_excludes_trace_files():
    """Trace files (*_llm_trace.jsonl) should NOT be included in the merge."""
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)

        events = game_dir / "game_events.jsonl"
        events.write_text(json.dumps({"ts": "2024-06-15T10:00:01.000-07:00", "type": "game_start"}) + "\n")

        # Write an LLM trace file — should NOT appear in merge
        trace = game_dir / "alice_llm_trace.jsonl"
        trace.write_text(
            json.dumps(
                {
                    "ts": "2024-06-15T10:00:02.000-07:00",
                    "type": "llm_call",
                    "player": "alice",
                }
            )
            + "\n"
        )

        merge_game_log(game_dir)

        merged = game_dir / "game.jsonl"
        assert merged.exists()
        lines = merged.read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["type"] == "game_start"
