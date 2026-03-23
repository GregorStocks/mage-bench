"""Port availability checking."""

import fcntl
import os
import socket
import tempfile
import time
from types import TracebackType


class PortReservation:
    """Holds flock-based reservations on one or more ports.

    The locks prevent concurrent processes from selecting the same port.
    Release after the Java server has bound the port.
    """

    def __init__(self, port: int, lock_fds: list[int]) -> None:
        self.port = port
        self._lock_fds = lock_fds

    def release(self) -> None:
        """Release all held locks (idempotent)."""
        for fd in self._lock_fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self._lock_fds.clear()

    def __enter__(self) -> "PortReservation":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.release()


def _try_lock_port(port: int) -> int | None:
    """Try to acquire an exclusive flock on a per-port lock file.

    Returns the open file descriptor on success, or None if another
    process already holds the lock.
    """
    lock_path = _lock_path_for_port(port)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None


def _lock_path_for_port(port: int) -> str:
    """Return the lock-file path for a reserved port."""
    return os.path.join(tempfile.gettempdir(), f"mage-port-{port}.lock")


def is_port_in_use(host: str, port: int, timeout: float = 1.0) -> bool:
    """Check if a port is in use by attempting to connect (something is listening)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        result = sock.connect_ex((host, port))
        return result == 0  # Zero means connection succeeded = port in use
    finally:
        sock.close()


def can_bind_port(port: int) -> bool:
    """Check if we can actually bind to a port. More reliable than connect-based
    checks since it detects TIME_WAIT and other states that prevent binding."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def find_available_port(start_port: int, max_attempts: int = 100) -> PortReservation:
    """Find an available port starting from start_port, holding flock reservations.

    Returns a PortReservation that holds exclusive locks on the primary port
    and the secondary port (port+8). Caller must release() the reservation
    after the server has bound the port.
    """
    for offset in range(max_attempts):
        port = start_port + offset
        fd_primary = _try_lock_port(port)
        if fd_primary is None:
            continue
        fd_secondary = _try_lock_port(port + 8)
        if fd_secondary is None:
            os.close(fd_primary)
            continue
        if can_bind_port(port) and can_bind_port(port + 8):
            return PortReservation(port, [fd_primary, fd_secondary])
        os.close(fd_primary)
        os.close(fd_secondary)
    raise RuntimeError(f"No available port found in range {start_port}-{start_port + max_attempts}")


def wait_for_port(host: str, port: int, timeout: int, poll_interval: float = 1.0) -> bool:
    """Wait for a port to become reachable (server started)."""
    start = time.time()
    while time.time() - start < timeout:
        if is_port_in_use(host, port):
            return True
        time.sleep(poll_interval)
    return False
