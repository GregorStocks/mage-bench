"""Unit tests for HTTP-based spectator readiness and game-end detection."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from tests.golden_helpers import _wait_for_game_end_http, _wait_for_game_ready, _wait_for_health


class _HealthHandler(BaseHTTPRequestHandler):
    """Mock observer health server for testing."""

    # Class-level configuration set by tests
    lobby_ready_delay: float = 0
    game_ready_delay: float = 0
    game_end_delay: float = 0

    def do_GET(self) -> None:
        if self.path.startswith("/health"):
            if self.lobby_ready_delay > 0:
                time.sleep(self.lobby_ready_delay)
            self._send_json(200, {"status": "ready"})
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if self.path == "/wait-for-ready":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            timeout = body.get("timeout", 240)
            if self.game_ready_delay > 0:
                time.sleep(min(self.game_ready_delay, timeout))
                if self.game_ready_delay > timeout:
                    self._send_json(408, {"ready": False, "error": "timeout"})
                    return
            self._send_json(200, {"ready": True, "tableId": "test-table-id"})
        elif self.path == "/wait-for-game-end":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            timeout = body.get("timeout", 30)
            if self.game_end_delay > 0:
                time.sleep(min(self.game_end_delay, timeout))
                if self.game_end_delay > timeout:
                    self._send_json(408, {"done": False, "error": "timeout"})
                    return
            self._send_json(200, {"done": True})
        else:
            self.send_error(404)

    def _send_json(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # Suppress request logs during tests


@pytest.fixture()
def mock_health_server():
    """Start a mock health server and yield its port."""
    # Reset delays
    _HealthHandler.lobby_ready_delay = 0
    _HealthHandler.game_ready_delay = 0
    _HealthHandler.game_end_delay = 0

    server = HTTPServer(("127.0.0.1", 0), _HealthHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port, server
    server.shutdown()


class TestWaitForHealth:
    def test_lobby_ready_immediately(self, mock_health_server: tuple[int, HTTPServer]) -> None:
        port, _server = mock_health_server
        _wait_for_health(port, timeout=5)

    def test_lobby_ready_after_delay(self, mock_health_server: tuple[int, HTTPServer]) -> None:
        port, _server = mock_health_server
        _HealthHandler.lobby_ready_delay = 0.3
        t0 = time.monotonic()
        _wait_for_health(port, timeout=5)
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.2
        assert elapsed < 2.0


class TestWaitForGameReady:
    def test_game_ready_immediately(self, mock_health_server: tuple[int, HTTPServer]) -> None:
        port, _server = mock_health_server
        table_id = _wait_for_game_ready(port, Path("/tmp/test-game"), timeout=5)
        assert table_id == "test-table-id"

    def test_game_ready_after_delay(self, mock_health_server: tuple[int, HTTPServer]) -> None:
        port, _server = mock_health_server
        _HealthHandler.game_ready_delay = 0.3
        t0 = time.monotonic()
        table_id = _wait_for_game_ready(port, Path("/tmp/test-game"), timeout=5)
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.2
        assert elapsed < 2.0
        assert table_id == "test-table-id"

    def test_game_ready_timeout(self, mock_health_server: tuple[int, HTTPServer]) -> None:
        port, _server = mock_health_server
        _HealthHandler.game_ready_delay = 10
        with pytest.raises(RuntimeError, match="Wait-for-ready failed"):
            _wait_for_game_ready(port, Path("/tmp/test-game"), timeout=1)


class TestWaitForGameEnd:
    def test_game_end_immediately(self, mock_health_server: tuple[int, HTTPServer]) -> None:
        port, _server = mock_health_server
        _wait_for_game_end_http(port, Path("/tmp/test-game"), timeout=5)

    def test_game_end_after_delay(self, mock_health_server: tuple[int, HTTPServer]) -> None:
        port, _server = mock_health_server
        _HealthHandler.game_end_delay = 0.3
        t0 = time.monotonic()
        _wait_for_game_end_http(port, Path("/tmp/test-game"), timeout=5)
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.2
        assert elapsed < 2.0

    def test_game_end_timeout(self, mock_health_server: tuple[int, HTTPServer]) -> None:
        port, _server = mock_health_server
        _HealthHandler.game_end_delay = 10
        with pytest.raises(RuntimeError, match="Wait-for-game-end failed"):
            _wait_for_game_end_http(port, Path("/tmp/test-game"), timeout=1)
