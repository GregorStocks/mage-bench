"""Unit tests for golden cleanup recovery after postgame bridge failures."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from tests import golden_helpers


class _FakeSession:
    def __init__(self, *, concede_error: RuntimeError | None = None) -> None:
        self.calls: list[tuple[str, dict, int | None]] = []
        self._concede_error = concede_error

    def call_tool(self, name: str, arguments: dict | None = None, timeout: int | None = None) -> str:
        args = arguments or {}
        self.calls.append((name, args, timeout))
        if name == "concede" and self._concede_error is not None:
            raise self._concede_error
        return "{}"


class _FakeBridgeManager:
    def __init__(
        self,
        session: _FakeSession,
        label: str,
        *,
        restart_error: RuntimeError | None = None,
    ) -> None:
        self.session = session
        self._label = label
        self.restart_calls = 0
        self.reconnect_checks: list[str] = []
        self.events: list[str] = []
        self._restart_error = restart_error

    def is_healthy(self) -> bool:
        return True

    def ensure_healthy(self) -> None:
        raise AssertionError("ensure_healthy should not be called in this test")

    def assert_clean_reconnect(self, context: str) -> None:
        self.reconnect_checks.append(context)

    def capture_log_offsets(self) -> tuple[int, int]:
        return (0, 0)

    def write_test_log_snapshots(self, _test_name: str, _offsets: tuple[int, int]) -> None:
        self.events.append("snapshot")

    def restart(self) -> None:
        self.events.append("restart")
        self.restart_calls += 1
        if self._restart_error is not None:
            raise self._restart_error


class _FakeSpectator:
    def wait_for_watching(self, _game_dir: Path) -> None:
        pass

    def wait_for_game_end(self, _game_dir: Path) -> None:
        pass


@pytest.fixture
def stubbed_golden(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(golden_helpers, "_send_spectator_command", lambda *_args, **_kwargs: "test-table")
    monkeypatch.setattr(golden_helpers, "_run_pilot_on_bridge", lambda *_args, **_kwargs: [{"role": "assistant"}])
    monkeypatch.setattr(golden_helpers, "record_registered_rss_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(golden_helpers, "build_export", lambda _game_dir: {"ok": True})
    monkeypatch.setattr(golden_helpers, "assert_golden_prompt", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(golden_helpers, "assert_golden_export", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(golden_helpers, "_script_blunder_indices", lambda _script: [])
    monkeypatch.setattr(golden_helpers, "timed_phase", lambda *_args, **_kwargs: contextlib.nullcontext())
    return tmp_path / "game"


def test_run_golden_scenario_restarts_bridge_after_benign_player_b_replay_error(
    monkeypatch: pytest.MonkeyPatch, stubbed_golden: Path, tmp_path: Path
) -> None:
    player_a = _FakeSession()
    player_b = _FakeSession()
    bridge_a = _FakeBridgeManager(player_a, "bridge")
    bridge_b = _FakeBridgeManager(player_b, "opponent")
    spectator = _FakeSpectator()

    def _raise_player_b_error(*_args, **_kwargs) -> None:
        raise RuntimeError("Bridge RPC error after 120.0s for tools/call(pass_priority): timed out")

    monkeypatch.setattr(golden_helpers, "_run_opponent_autopass", _raise_player_b_error)

    prompt = golden_helpers.run_golden_scenario(
        server="localhost",
        port=17171,
        project_root=tmp_path,
        game_dir=stubbed_golden,
        deck_a="puppeteer/tests/decks/bolt_and_burn.dck",
        deck_b="puppeteer/tests/decks/filler_opponent.dck",
        script_a=[{"name": "pass_priority"}],
        golden_name="cleanup_recovery",
        bridge_a=bridge_a,
        bridge_b=bridge_b,
        spectator=spectator,
    )

    assert prompt == [{"role": "assistant"}]
    assert [call[0] for call in player_a.calls] == ["join_table", "concede"]
    assert [call[0] for call in player_b.calls] == ["join_table"]
    assert bridge_a.restart_calls == 0
    assert bridge_b.restart_calls == 1
    assert bridge_a.events == ["snapshot"]
    assert bridge_b.events == ["snapshot", "restart"]


def test_run_golden_scenario_restarts_bridge_when_cleanup_concede_times_out(
    monkeypatch: pytest.MonkeyPatch, stubbed_golden: Path, tmp_path: Path
) -> None:
    player_a = _FakeSession()
    player_b = _FakeSession(concede_error=RuntimeError("cleanup timed out"))
    bridge_a = _FakeBridgeManager(player_a, "bridge")
    bridge_b = _FakeBridgeManager(player_b, "opponent")
    spectator = _FakeSpectator()

    monkeypatch.setattr(golden_helpers, "_run_opponent_autopass", lambda *_args, **_kwargs: None)

    prompt = golden_helpers.run_golden_scenario(
        server="localhost",
        port=17171,
        project_root=tmp_path,
        game_dir=stubbed_golden,
        deck_a="puppeteer/tests/decks/bolt_and_burn.dck",
        deck_b="puppeteer/tests/decks/filler_opponent.dck",
        script_a=[{"name": "pass_priority"}],
        golden_name="cleanup_recovery",
        bridge_a=bridge_a,
        bridge_b=bridge_b,
        spectator=spectator,
    )

    assert prompt == [{"role": "assistant"}]
    assert [call[0] for call in player_a.calls] == ["join_table", "concede"]
    assert [call[0] for call in player_b.calls] == ["join_table", "concede"]
    assert bridge_a.restart_calls == 0
    assert bridge_b.restart_calls == 1
    assert bridge_a.events == ["snapshot"]
    assert bridge_b.events == ["snapshot", "restart"]


def test_run_golden_scenario_preserves_primary_replay_failure_over_cleanup_error(
    monkeypatch: pytest.MonkeyPatch, stubbed_golden: Path, tmp_path: Path
) -> None:
    player_a = _FakeSession(concede_error=RuntimeError("cleanup timed out"))
    player_b = _FakeSession()
    bridge_a = _FakeBridgeManager(player_a, "bridge")
    bridge_b = _FakeBridgeManager(player_b, "opponent")
    spectator = _FakeSpectator()

    monkeypatch.setattr(
        golden_helpers,
        "_run_pilot_on_bridge",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("player A replay failed")),
    )
    monkeypatch.setattr(golden_helpers, "_run_opponent_autopass", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="player A replay failed"):
        golden_helpers.run_golden_scenario(
            server="localhost",
            port=17171,
            project_root=tmp_path,
            game_dir=stubbed_golden,
            deck_a="puppeteer/tests/decks/bolt_and_burn.dck",
            deck_b="puppeteer/tests/decks/filler_opponent.dck",
            script_a=[{"name": "pass_priority"}],
            golden_name="cleanup_recovery",
            bridge_a=bridge_a,
            bridge_b=bridge_b,
            spectator=spectator,
        )

    assert bridge_a.restart_calls == 1
    assert bridge_b.restart_calls == 0


def test_run_golden_scenario_preserves_setup_failure_and_still_cleans_up(
    monkeypatch: pytest.MonkeyPatch, stubbed_golden: Path, tmp_path: Path
) -> None:
    player_a = _FakeSession()
    player_b = _FakeSession()
    bridge_a = _FakeBridgeManager(player_a, "bridge")
    bridge_b = _FakeBridgeManager(player_b, "opponent")
    spectator = _FakeSpectator()

    monkeypatch.setattr(
        golden_helpers,
        "_send_spectator_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("setup failed")),
    )

    with pytest.raises(RuntimeError, match="setup failed"):
        golden_helpers.run_golden_scenario(
            server="localhost",
            port=17171,
            project_root=tmp_path,
            game_dir=stubbed_golden,
            deck_a="puppeteer/tests/decks/bolt_and_burn.dck",
            deck_b="puppeteer/tests/decks/filler_opponent.dck",
            script_a=[{"name": "pass_priority"}],
            golden_name="cleanup_recovery",
            bridge_a=bridge_a,
            bridge_b=bridge_b,
            spectator=spectator,
        )

    assert [call[0] for call in player_a.calls] == ["concede"]
    assert [call[0] for call in player_b.calls] == ["concede"]
    assert bridge_a.restart_calls == 0
    assert bridge_b.restart_calls == 0


def test_run_golden_scenario_preserves_primary_failure_when_restart_fails(
    monkeypatch: pytest.MonkeyPatch, stubbed_golden: Path, tmp_path: Path
) -> None:
    player_a = _FakeSession(concede_error=RuntimeError("cleanup timed out"))
    player_b = _FakeSession()
    bridge_a = _FakeBridgeManager(
        player_a,
        "bridge",
        restart_error=RuntimeError("restart failed"),
    )
    bridge_b = _FakeBridgeManager(player_b, "opponent")
    spectator = _FakeSpectator()

    monkeypatch.setattr(
        golden_helpers,
        "_run_pilot_on_bridge",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("player A replay failed")),
    )
    monkeypatch.setattr(golden_helpers, "_run_opponent_autopass", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="player A replay failed") as excinfo:
        golden_helpers.run_golden_scenario(
            server="localhost",
            port=17171,
            project_root=tmp_path,
            game_dir=stubbed_golden,
            deck_a="puppeteer/tests/decks/bolt_and_burn.dck",
            deck_b="puppeteer/tests/decks/filler_opponent.dck",
            script_a=[{"name": "pass_priority"}],
            golden_name="cleanup_recovery",
            bridge_a=bridge_a,
            bridge_b=bridge_b,
            spectator=spectator,
        )

    notes = getattr(excinfo.value, "__notes__", [])
    assert any("restart failed" in note for note in notes)
    assert bridge_a.restart_calls == 1
    assert bridge_b.restart_calls == 0


def test_run_golden_scenario_fails_successful_scenario_when_restart_fails(
    monkeypatch: pytest.MonkeyPatch, stubbed_golden: Path, tmp_path: Path
) -> None:
    player_a = _FakeSession()
    player_b = _FakeSession(concede_error=RuntimeError("cleanup timed out"))
    bridge_a = _FakeBridgeManager(player_a, "bridge")
    bridge_b = _FakeBridgeManager(
        player_b,
        "opponent",
        restart_error=RuntimeError("restart failed"),
    )
    spectator = _FakeSpectator()

    monkeypatch.setattr(golden_helpers, "_run_opponent_autopass", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="Golden cleanup restart failed after scenario success") as excinfo:
        golden_helpers.run_golden_scenario(
            server="localhost",
            port=17171,
            project_root=tmp_path,
            game_dir=stubbed_golden,
            deck_a="puppeteer/tests/decks/bolt_and_burn.dck",
            deck_b="puppeteer/tests/decks/filler_opponent.dck",
            script_a=[{"name": "pass_priority"}],
            golden_name="cleanup_recovery",
            bridge_a=bridge_a,
            bridge_b=bridge_b,
            spectator=spectator,
        )

    assert "opponent: restart failed" in str(excinfo.value)
    assert bridge_a.restart_calls == 0
    assert bridge_b.restart_calls == 1


def test_bridge_manager_restart_wraps_start_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bridge = golden_helpers.BridgeManager(
        server="localhost",
        port=17171,
        project_root=tmp_path,
    )

    monkeypatch.setattr(bridge, "stop", lambda: None)
    monkeypatch.setattr(golden_helpers.time, "sleep", lambda _seconds: None)

    def _raise_start_failure() -> None:
        raise AssertionError("start failed")

    monkeypatch.setattr(bridge, "start", _raise_start_failure)

    with pytest.raises(RuntimeError, match="Bridge restart failed") as excinfo:
        bridge.restart()

    assert isinstance(excinfo.value.__cause__, AssertionError)
