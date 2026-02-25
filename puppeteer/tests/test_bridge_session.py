"""Unit tests for BridgeSession and PotatoProcess wrappers."""

import io
import json
import subprocess
from unittest.mock import MagicMock

import pytest

from tests.golden_helpers import BridgeSession, PotatoProcess


def _make_mock_proc(responses: list[dict] | None = None) -> subprocess.Popen:
    """Create a mock Popen with real byte-stream stdin/stdout.

    BridgeSession wraps stdin/stdout with io.TextIOWrapper, so we need
    actual byte buffers rather than plain MagicMocks.
    """
    proc = MagicMock(spec=subprocess.Popen)

    # stdin: writable byte buffer
    proc.stdin = io.BytesIO()

    # stdout: readable byte buffer pre-loaded with JSON-RPC responses
    stdout_data = b""
    if responses:
        for r in responses:
            stdout_data += json.dumps(r).encode("utf-8") + b"\n"
    proc.stdout = io.BytesIO(stdout_data)

    return proc


class TestBridgeSession:
    def test_initialize_sends_correct_rpc(self):
        response = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}
        proc = _make_mock_proc([response])

        bridge = BridgeSession(proc)
        result = bridge.initialize()

        assert result == {"protocolVersion": "2024-11-05"}
        # Verify the request was written
        proc.stdin.seek(0)
        req = json.loads(proc.stdin.read().decode("utf-8").strip())
        assert req["method"] == "initialize"
        assert req["id"] == 1

    def test_call_tool_returns_text(self):
        tool_result = {"content": [{"type": "text", "text": '{"game_seq":5}'}], "isError": False}
        response = {"jsonrpc": "2.0", "id": 1, "result": tool_result}
        proc = _make_mock_proc([response])

        bridge = BridgeSession(proc)
        text = bridge.call_tool("get_game_state", {})

        assert text == '{"game_seq":5}'

    def test_list_tools_returns_names(self):
        tools_result = {"tools": [{"name": "pass_priority"}, {"name": "get_game_state"}]}
        response = {"jsonrpc": "2.0", "id": 1, "result": tools_result}
        proc = _make_mock_proc([response])

        bridge = BridgeSession(proc)
        names = bridge.list_tools()

        assert names == ["pass_priority", "get_game_state"]

    def test_rpc_error_raises(self):
        response = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "boom"}}
        proc = _make_mock_proc([response])

        bridge = BridgeSession(proc)
        with pytest.raises(RuntimeError, match="boom"):
            bridge.call_tool("bad_tool", {})

    def test_sequential_ids_increment(self):
        responses = [
            {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "ok"}]}},
        ]
        proc = _make_mock_proc(responses)

        bridge = BridgeSession(proc)
        bridge.initialize()
        bridge.call_tool("pass_priority", {})

        proc.stdin.seek(0)
        lines = [line for line in proc.stdin.read().decode("utf-8").strip().split("\n") if line]
        assert len(lines) == 2
        assert json.loads(lines[0])["id"] == 1
        assert json.loads(lines[1])["id"] == 2

    def test_close_does_not_raise(self):
        proc = _make_mock_proc([])
        bridge = BridgeSession(proc)
        bridge.close()  # Should not raise

    def test_stdout_eof_raises(self):
        """Bridge detects when the JVM closes stdout unexpectedly."""
        proc = _make_mock_proc([])  # empty stdout
        bridge = BridgeSession(proc)
        with pytest.raises(AssertionError, match="closed stdout"):
            bridge.call_tool("pass_priority", {})


class TestPotatoProcess:
    def test_join_next_game_writes_deck_path(self):
        proc = MagicMock(spec=subprocess.Popen)
        proc.stdin = io.BytesIO()

        potato = PotatoProcess(proc)
        potato.join_next_game("/path/to/deck.dck")

        proc.stdin.seek(0)
        text = proc.stdin.read().decode("utf-8")
        assert text.strip() == "/path/to/deck.dck"

    def test_close_does_not_raise(self):
        proc = MagicMock(spec=subprocess.Popen)
        proc.stdin = io.BytesIO()
        potato = PotatoProcess(proc)
        potato.close()  # Should not raise
