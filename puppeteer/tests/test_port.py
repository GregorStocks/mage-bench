"""Tests for port availability checking."""

import socket
from unittest.mock import patch

import pytest

from puppeteer.port import (
    _lock_path_for_port,
    can_bind_port,
    find_available_port,
    is_port_in_use,
    wait_for_port,
)


def test_is_port_in_use_open():
    """A port with a listening socket should be detected as in use."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert is_port_in_use("127.0.0.1", port) is True
    finally:
        server.close()


def test_is_port_in_use_closed():
    """A port with no listener should not be detected as in use."""
    # Bind to get a free port, then close immediately
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.close()
    assert is_port_in_use("127.0.0.1", port) is False


def test_can_bind_port_free():
    """Should return True for a port we can bind to."""
    # Find a free port by binding to 0
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    sock.close()
    assert can_bind_port(port) is True


def test_can_bind_port_occupied():
    """Should return False for a port already bound."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("", 0))
    port = server.getsockname()[1]
    try:
        # Port is bound but not with SO_REUSEPORT, so a second bind should fail
        assert can_bind_port(port) is False
    finally:
        server.close()


def test_find_available_port():
    """Should find an available port and return a PortReservation."""
    reservation = find_available_port(19000, max_attempts=100)
    try:
        assert reservation.port >= 19000
        assert reservation.port < 19100
    finally:
        reservation.release()


def test_lock_path_uses_tempdir():
    """Port lock files live under the active temp directory."""
    with patch("puppeteer.port.tempfile.gettempdir", return_value="/var/tmp/mage-tests"):
        assert _lock_path_for_port(19000) == "/var/tmp/mage-tests/mage-port-19000.lock"


def test_find_available_port_exhausted():
    """Should raise RuntimeError when all ports are locked."""
    with (
        patch("puppeteer.port._try_lock_port", return_value=None),
        pytest.raises(RuntimeError, match="No available port found"),
    ):
        find_available_port(19000, max_attempts=5)


def test_port_reservation_release():
    """Releasing a reservation makes the port re-acquirable."""
    reservation = find_available_port(19200, max_attempts=100)
    port = reservation.port
    reservation.release()

    # Should be able to lock the same port again
    reservation2 = find_available_port(port, max_attempts=1)
    try:
        assert reservation2.port == port
    finally:
        reservation2.release()


def test_port_reservation_context_manager():
    """PortReservation works as a context manager."""
    with find_available_port(19300, max_attempts=100) as reservation:
        assert reservation.port >= 19300
    # After exiting, locks are released — fds list is empty
    assert reservation._lock_fds == []


def test_port_reservation_prevents_duplicate():
    """A held reservation forces the next caller to skip that port."""
    reservation = find_available_port(19400, max_attempts=100)
    try:
        # The next call starting at the same port should get a different one
        reservation2 = find_available_port(reservation.port, max_attempts=100)
        try:
            assert reservation2.port != reservation.port
        finally:
            reservation2.release()
    finally:
        reservation.release()


def test_port_reservation_double_release():
    """Calling release() twice is safe (idempotent)."""
    reservation = find_available_port(19500, max_attempts=100)
    reservation.release()
    reservation.release()  # Should not raise
    assert reservation._lock_fds == []


def test_wait_for_port_immediate():
    """Should return True immediately when port is already listening."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert wait_for_port("127.0.0.1", port, timeout=2) is True
    finally:
        server.close()


def test_wait_for_port_timeout():
    """Should return False after timeout when port never opens."""
    with patch("puppeteer.port.is_port_in_use", return_value=False), patch("puppeteer.port.time") as mock_time:
        # Simulate time passing: first call returns 0, second returns timeout+1
        mock_time.time.side_effect = [0, 0, 2]
        mock_time.sleep = lambda _seconds: None
        assert wait_for_port("127.0.0.1", 19999, timeout=1) is False
