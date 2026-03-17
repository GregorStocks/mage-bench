"""Process lifecycle management with signal handling."""

import atexit
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import IO, Any

import psutil

_PREFER_OOM_KILL_SCORE_ADJ = 1000


def _write_oom_score_adj(score_adj: int) -> None:
    """Set the current process's Linux OOM score adjustment."""
    assert -1000 <= score_adj <= 1000, f"oom_score_adj out of range: {score_adj}"
    with open("/proc/self/oom_score_adj", "w", encoding="ascii") as fh:
        fh.write(f"{score_adj}\n")


def jvm_oom_preexec_fn() -> Callable[[], object] | None:
    """Return a Linux ``preexec_fn`` that biases OOM kills toward launched JVMs."""
    if sys.platform != "linux":
        return None

    def _preexec() -> None:
        _write_oom_score_adj(_PREFER_OOM_KILL_SCORE_ADJ)

    return _preexec


def kill_tree(pid: int) -> None:
    """Kill a process and all its children."""
    try:
        parent = psutil.Process(pid)

        # If the target process is in a different process group, try
        # terminating the entire group (avoid killing our own group).
        if hasattr(os, "getpgrp") and hasattr(os, "getpgid") and hasattr(os, "killpg"):
            try:
                parent_pgid = os.getpgrp()
                child_pgid = os.getpgid(pid)
                if child_pgid != parent_pgid:
                    os.killpg(child_pgid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass

        children = parent.children(recursive=True)

        # Terminate children first, then parent
        for child in children:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass

        try:
            parent.terminate()
        except psutil.NoSuchProcess:
            pass

        # Wait briefly then force kill if needed
        _gone, alive = psutil.wait_procs([*children, parent], timeout=3)
        for p in alive:
            try:
                p.kill()
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        pass


class ProcessManager:
    """Manages subprocess lifecycle with proper cleanup on signals.

    Ensures all child processes are terminated when:
    - The parent receives SIGINT, SIGTERM, or SIGHUP
    - The parent exits normally
    - The parent exits due to an unhandled exception
    """

    def __init__(self) -> None:
        self._processes: list[tuple[subprocess.Popen, IO | None]] = []
        self._lock = threading.RLock()
        self._cleaned_up = False

        self._setup_signal_handlers()
        # Register atexit handler for cleanup on normal exit or unhandled exceptions
        atexit.register(self.cleanup)

    def _setup_signal_handlers(self) -> None:
        """Register signal handlers for SIGINT, SIGTERM, and SIGHUP."""
        signal.signal(signal.SIGINT, self._sigint_handler)
        signal.signal(signal.SIGTERM, self._fatal_signal_handler)
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, self._fatal_signal_handler)

    def _sigint_handler(self, _signum: int, _frame: FrameType | None) -> None:
        """Handle Ctrl-C: kill children but let the main flow continue.

        First Ctrl-C kills child processes and returns so the caller can
        run post-game cleanup (cost summary, YouTube upload, etc.).
        A second Ctrl-C triggers the default handler (immediate exit).
        """
        print("\nReceived SIGINT, stopping all processes...")
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        self.cleanup()

    def _fatal_signal_handler(self, signum: int, _frame: FrameType | None) -> None:
        """Handle SIGTERM/SIGHUP: cleanup and exit immediately."""
        print(f"\nReceived signal {signum}, stopping all processes...")
        self.cleanup()
        sys.exit(0)

    def _start_tracked_process(
        self,
        args: list[str],
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        log_file: Path | None = None,
        preexec_fn: Callable[[], object] | None = None,
    ) -> subprocess.Popen:
        """Start a subprocess with an optional ``preexec_fn`` and track it for cleanup.

        Processes are kept in the same process group as the parent so they
        receive signals when the parent is killed.
        """
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)

        log_fh: IO[Any] | None = None
        stdout: int | IO[Any]
        stderr: int | IO[Any]
        if log_file:
            log_fh = open(log_file, "w")
            stdout = log_fh
            stderr = subprocess.STDOUT
        else:
            stdout = subprocess.PIPE
            stderr = subprocess.PIPE

        try:
            if preexec_fn is None:
                proc = subprocess.Popen(
                    args,
                    cwd=cwd,
                    env=merged_env,
                    stdout=stdout,
                    stderr=stderr,
                    # Don't use start_new_session=True - keep processes in same group
                    # so they receive signals when parent is killed
                )
            else:
                proc = subprocess.Popen(
                    args,
                    cwd=cwd,
                    env=merged_env,
                    stdout=stdout,
                    stderr=stderr,
                    preexec_fn=preexec_fn,
                    # Don't use start_new_session=True - keep processes in same group
                    # so they receive signals when parent is killed
                )
        except Exception:
            if log_fh:
                log_fh.close()
            raise

        with self._lock:
            self._processes.append((proc, log_fh))

        return proc

    def start_process(
        self,
        args: list[str],
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        log_file: Path | None = None,
    ) -> subprocess.Popen:
        """Start a non-JVM subprocess and track it for cleanup."""
        return self._start_tracked_process(
            args=args,
            cwd=cwd,
            env=env,
            log_file=log_file,
        )

    def start_jvm_process(
        self,
        args: list[str],
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        log_file: Path | None = None,
    ) -> subprocess.Popen:
        """Start a JVM-launching subprocess and bias Linux OOM kills toward it."""
        return self._start_tracked_process(
            args=args,
            cwd=cwd,
            env=env,
            log_file=log_file,
            preexec_fn=jvm_oom_preexec_fn(),
        )

    def cleanup(self) -> None:
        """Terminate all tracked processes and close log file handles."""
        with self._lock:
            if self._cleaned_up:
                return
            self._cleaned_up = True

            for proc, _fh in self._processes:
                if proc.poll() is None:  # Still running
                    print(f"Killing process tree rooted at PID {proc.pid}")
                    kill_tree(proc.pid)

            for _proc, fh in self._processes:
                if fh is not None:
                    try:
                        fh.close()
                    except OSError:
                        pass

            self._processes.clear()
