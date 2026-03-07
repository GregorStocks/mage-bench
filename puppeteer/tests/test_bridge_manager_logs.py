"""Unit tests for golden bridge JVM lifecycle helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import golden_helpers
from tests.golden_helpers import BridgeManager

GAME_START_1 = "[08:00:00] INFO [TestPlayer] Game started: gameId=11111111-1111-1111-1111-111111111111, playerId=p1"
GAME_START_2 = "[08:00:01] INFO [TestPlayer] Game started: gameId=22222222-2222-2222-2222-222222222222, playerId=p2"
STALE_GAME_INIT = (
    "[08:00:01] WARN [TestPlayer] Ignoring GAME_INIT for non-current game "
    "22222222-2222-2222-2222-222222222222 "
    "(currentGameId=11111111-1111-1111-1111-111111111111)"
)


def _make_manager(tmp_path: Path, label: str = "bridge") -> BridgeManager:
    return BridgeManager(
        server="localhost",
        port=17171,
        project_root=tmp_path,
        allowed_sets="all",
        username="TestPlayer",
        label=label,
    )


class TestBridgeLogRotation:
    def test_prepare_live_log_path_rotates_existing_live_log(self, tmp_path: Path) -> None:
        manager = _make_manager(tmp_path)
        log_dir = tmp_path / "tmp" / "golden-bridge"
        log_dir.mkdir(parents=True)
        live_log = log_dir / "bridge.log"

        live_log.write_text("first run\n", encoding="utf-8")
        next_live_log = manager._prepare_live_log_path()

        assert next_live_log == live_log
        assert not live_log.exists()
        assert (log_dir / "bridge.1.log").read_text(encoding="utf-8") == "first run\n"

        live_log.write_text("second run\n", encoding="utf-8")
        manager._prepare_live_log_path()

        assert (log_dir / "bridge.2.log").read_text(encoding="utf-8") == "second run\n"

    def test_prepare_live_log_path_rotates_jsonl_artifacts(self, tmp_path: Path) -> None:
        manager = _make_manager(tmp_path)
        log_dir = tmp_path / "tmp" / "golden-bridge"
        log_dir.mkdir(parents=True)
        live_log = log_dir / "bridge-events.jsonl"

        live_log.write_text('{"method":"GAME_SELECT"}\n', encoding="utf-8")
        next_live_log = manager._prepare_live_log_path("bridge-events.jsonl")

        assert next_live_log == live_log
        assert not live_log.exists()
        assert (log_dir / "bridge-events.1.jsonl").read_text(encoding="utf-8") == '{"method":"GAME_SELECT"}\n'

    def test_start_passes_bridge_jsonl_log_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        manager = _make_manager(tmp_path)
        captured_props: dict[str, str] = {}

        class _FakePortReservation:
            port = 19042

            def release(self) -> None:
                pass

        class _FakePopen:
            pid = 12345

        class _FakeBridgeSession:
            def __init__(self, _url: str) -> None:
                pass

            def initialize(self) -> None:
                pass

        def _fake_build_java_cmd(_classpath: str, _main_class: str, system_props: dict[str, str]) -> list[str]:
            captured_props.update(system_props)
            return ["java", "-version"]

        monkeypatch.setattr(golden_helpers, "find_available_port", lambda *_args, **_kwargs: _FakePortReservation())
        monkeypatch.setattr(golden_helpers, "compute_module_classpath", lambda *_args, **_kwargs: "/fake/classpath")
        monkeypatch.setattr(golden_helpers, "_build_java_cmd", _fake_build_java_cmd)
        monkeypatch.setattr(golden_helpers, "wait_for_port", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(golden_helpers, "BridgeSession", _FakeBridgeSession)
        monkeypatch.setattr(golden_helpers.subprocess, "Popen", lambda *_args, **_kwargs: _FakePopen())

        manager.start()

        assert captured_props["xmage.bridge.bridgelog"] == str(
            tmp_path / "tmp" / "golden-bridge" / "bridge-events.jsonl"
        )


class TestReconnectValidation:
    def test_noop_without_restart(self, tmp_path: Path) -> None:
        manager = _make_manager(tmp_path)
        log_path = tmp_path / "bridge.log"
        log_path.write_text(GAME_START_1 + "\n", encoding="utf-8")
        manager._current_log_path = log_path

        manager.assert_clean_reconnect("golden")

    def test_accepts_single_started_game_after_restart(self, tmp_path: Path) -> None:
        manager = _make_manager(tmp_path)
        log_path = tmp_path / "bridge.log"
        log_path.write_text(GAME_START_1 + "\n", encoding="utf-8")
        manager._current_log_path = log_path
        manager._needs_reconnect_validation = True

        manager.assert_clean_reconnect("golden")

        assert manager._needs_reconnect_validation is False

    def test_raises_when_restart_inherits_multiple_games(self, tmp_path: Path) -> None:
        manager = _make_manager(tmp_path)
        log_path = tmp_path / "bridge.log"
        log_path.write_text(
            "\n".join([GAME_START_1, GAME_START_2]) + "\n",
            encoding="utf-8",
        )
        manager._current_log_path = log_path
        manager._needs_reconnect_validation = True

        with pytest.raises(RuntimeError, match="restarted into leaked game state") as excinfo:
            manager.assert_clean_reconnect("multi_amount_combat/bridge_join")

        msg = str(excinfo.value)
        assert "11111111-1111-1111-1111-111111111111" in msg
        assert "22222222-2222-2222-2222-222222222222" in msg

    def test_raises_when_restart_receives_stale_callbacks(self, tmp_path: Path) -> None:
        manager = _make_manager(tmp_path)
        log_path = tmp_path / "bridge.log"
        log_path.write_text(
            "\n".join([GAME_START_1, STALE_GAME_INIT]) + "\n",
            encoding="utf-8",
        )
        manager._current_log_path = log_path
        manager._needs_reconnect_validation = True

        with pytest.raises(RuntimeError, match="staleCallbacks="):
            manager.assert_clean_reconnect("multi_amount_combat/bridge_join")

    def test_ensure_healthy_marks_restart_for_validation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        manager = _make_manager(tmp_path)
        monkeypatch.setattr(manager, "is_healthy", lambda: False)
        monkeypatch.setattr(manager, "stop", lambda: None)
        monkeypatch.setattr(manager, "start", lambda: None)
        monkeypatch.setattr(golden_helpers.time, "sleep", lambda _seconds: None)
        golden_helpers.clear_timings()

        manager.ensure_healthy()

        assert manager._needs_reconnect_validation is True
