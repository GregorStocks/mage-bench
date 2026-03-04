"""Unit tests for BridgeSession wrapper."""

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from tests.golden_helpers import BridgeSession


def _mock_http_response(data: dict) -> MagicMock:
    """Create a mock HTTP response with the given JSON data."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(data).encode("utf-8")
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestBridgeSession:
    @patch("urllib.request.urlopen")
    def test_initialize_sends_correct_rpc(self, mock_urlopen):
        response = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}
        mock_urlopen.return_value = _mock_http_response(response)

        bridge = BridgeSession("http://localhost:9999/mcp")
        result = bridge.initialize()

        assert result == {"protocolVersion": "2024-11-05"}
        # Verify the request was sent correctly
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        assert body["method"] == "initialize"
        assert body["id"] == 1

    @patch("urllib.request.urlopen")
    def test_call_tool_returns_text(self, mock_urlopen):
        tool_result = {"content": [{"type": "text", "text": '{"game_seq":5}'}], "isError": False}
        response = {"jsonrpc": "2.0", "id": 1, "result": tool_result}
        mock_urlopen.return_value = _mock_http_response(response)

        bridge = BridgeSession("http://localhost:9999/mcp")
        text = bridge.call_tool("get_game_state", {})

        assert text == '{"game_seq":5}'

    @patch("urllib.request.urlopen")
    def test_list_tools_returns_names(self, mock_urlopen):
        tools_result = {"tools": [{"name": "pass_priority"}, {"name": "get_game_state"}]}
        response = {"jsonrpc": "2.0", "id": 1, "result": tools_result}
        mock_urlopen.return_value = _mock_http_response(response)

        bridge = BridgeSession("http://localhost:9999/mcp")
        names = bridge.list_tools()

        assert names == ["pass_priority", "get_game_state"]

    @patch("urllib.request.urlopen")
    def test_rpc_error_raises(self, mock_urlopen):
        response = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "boom"}}
        mock_urlopen.return_value = _mock_http_response(response)

        bridge = BridgeSession("http://localhost:9999/mcp")
        with pytest.raises(RuntimeError, match="boom"):
            bridge.call_tool("bad_tool", {})

    @patch("urllib.request.urlopen")
    def test_sequential_ids_increment(self, mock_urlopen):
        responses = [
            _mock_http_response({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}),
            _mock_http_response({"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "ok"}]}}),
        ]
        mock_urlopen.side_effect = responses

        bridge = BridgeSession("http://localhost:9999/mcp")
        bridge.initialize()
        bridge.call_tool("pass_priority", {})

        # Verify sequential IDs in requests
        calls = mock_urlopen.call_args_list
        assert len(calls) == 2
        req1 = json.loads(calls[0][0][0].data.decode("utf-8"))
        req2 = json.loads(calls[1][0][0].data.decode("utf-8"))
        assert req1["id"] == 1
        assert req2["id"] == 2

    def test_close_does_not_raise(self):
        bridge = BridgeSession("http://localhost:9999/mcp")
        bridge.close()  # Should not raise

    @patch("urllib.request.urlopen")
    def test_http_error_raises(self, mock_urlopen):
        """Bridge detects when the HTTP request fails."""
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        bridge = BridgeSession("http://localhost:9999/mcp")
        with pytest.raises(RuntimeError, match="Connection refused"):
            bridge.call_tool("pass_priority", {})
