"""Tests for ProcessManager signal handling."""

import signal
import threading
from unittest.mock import Mock

import pytest

from puppeteer import process_manager
from puppeteer.process_manager import ProcessManager, jvm_oom_preexec_fn


@pytest.fixture(autouse=True)
def _restore_signal_handlers():
    """Save and restore signal handlers so ProcessManager doesn't leak."""
    old_int = signal.getsignal(signal.SIGINT)
    old_term = signal.getsignal(signal.SIGTERM)
    old_hup = signal.getsignal(signal.SIGHUP) if hasattr(signal, "SIGHUP") else None
    yield
    signal.signal(signal.SIGINT, old_int)
    signal.signal(signal.SIGTERM, old_term)
    if old_hup is not None:
        signal.signal(signal.SIGHUP, old_hup)


def test_sigint_does_not_exit():
    """First SIGINT should kill processes but not raise SystemExit."""
    pm = ProcessManager()
    # Calling the handler directly should NOT exit
    pm._sigint_handler(signal.SIGINT, None)
    # If we get here, no SystemExit was raised -- that's the point.


def test_sigint_restores_default_handler():
    """After first SIGINT, handler should be restored to SIG_DFL."""
    pm = ProcessManager()
    assert signal.getsignal(signal.SIGINT) == pm._sigint_handler
    pm._sigint_handler(signal.SIGINT, None)
    assert signal.getsignal(signal.SIGINT) == signal.SIG_DFL


def test_sigterm_exits():
    """SIGTERM should cleanup and exit."""
    pm = ProcessManager()
    with pytest.raises(SystemExit) as exc_info:
        pm._fatal_signal_handler(signal.SIGTERM, None)
    assert exc_info.value.code == 0


def test_cleanup_idempotent():
    """Second cleanup() call should be a no-op."""
    pm = ProcessManager()
    pm.cleanup()
    pm.cleanup()  # Should not raise


def test_uses_reentrant_lock():
    """ProcessManager should use RLock to avoid deadlock in signal handlers."""
    pm = ProcessManager()
    assert isinstance(pm._lock, type(threading.RLock()))


def test_jvm_oom_preexec_fn_linux(monkeypatch: pytest.MonkeyPatch):
    """Linux JVM launches should get a preexec hook that raises oom_score_adj."""
    calls: list[int] = []
    monkeypatch.setattr(process_manager.sys, "platform", "linux")
    monkeypatch.setattr(process_manager, "_write_oom_score_adj", calls.append)

    preexec_fn = jvm_oom_preexec_fn()

    assert preexec_fn is not None
    preexec_fn()
    assert calls == [1000]


def test_jvm_oom_preexec_fn_non_linux(monkeypatch: pytest.MonkeyPatch):
    """Non-Linux platforms should not get Linux-specific subprocess hooks."""
    monkeypatch.setattr(process_manager.sys, "platform", "darwin")
    assert jvm_oom_preexec_fn() is None


def test_start_jvm_process_passes_oom_preference_kwargs(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """JVM launches should always receive the Linux OOM-bias hook."""
    popen = Mock()
    popen.return_value = Mock()
    marker = object()

    monkeypatch.setattr(process_manager.subprocess, "Popen", popen)
    monkeypatch.setattr(process_manager, "jvm_oom_preexec_fn", lambda: marker)

    pm = ProcessManager()
    pm.start_jvm_process(["echo", "hi"], cwd=tmp_path)
    assert popen.call_args_list[0].kwargs["preexec_fn"] is marker

    pm.start_process(["echo", "hi"], cwd=tmp_path)
    assert "preexec_fn" not in popen.call_args_list[1].kwargs
