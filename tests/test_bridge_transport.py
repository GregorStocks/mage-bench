"""Tests for shared bridge launch argument assembly."""

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock

import httpx2

from magebench.pilot import bridge_transport


def test_build_bridge_launch_args_for_sleepwalker() -> None:
    launch_args = bridge_transport.build_bridge_launch_args(
        server="example.org",
        port=17171,
        username="Sleeper",
        deck_path=Path("/tmp/decks/sleeper.dck"),
        heap_size_mb=512,
    )

    assert launch_args.jvm_args == (
        "--add-opens=java.base/java.io=ALL-UNNAMED -Xmx512m -Dxmage.bridge.server=example.org -Dxmage.bridge.port=17171"
    )
    assert launch_args.mvn_args == [
        "-q",
        "-Dxmage.bridge.username=Sleeper",
        "-Dxmage.bridge.deck=/tmp/decks/sleeper.dck",
        "exec:java",
    ]


def test_build_bridge_launch_args_for_pilot_with_logs() -> None:
    game_dir = Path("/tmp/game-001")

    launch_args = bridge_transport.build_bridge_launch_args(
        server="localhost",
        port=17171,
        username="Pilot",
        deck_path=Path("/tmp/decks/pilot.dck"),
        heap_size_mb=512,
        error_log_path=game_dir / "Pilot_errors.log",
        bridge_log_path=game_dir / "Pilot_bridge.jsonl",
        max_interactions_per_turn=9,
    )

    assert launch_args.jvm_args == (
        "--add-opens=java.base/java.io=ALL-UNNAMED -Xmx512m -Dxmage.bridge.server=localhost -Dxmage.bridge.port=17171"
    )
    assert launch_args.mvn_args == [
        "-q",
        "-Dxmage.bridge.username=Pilot",
        "-Dxmage.bridge.deck=/tmp/decks/pilot.dck",
        "-Dxmage.bridge.errorlog=/tmp/game-001/Pilot_errors.log",
        "-Dxmage.bridge.bridgelog=/tmp/game-001/Pilot_bridge.jsonl",
        "-Dxmage.bridge.maxInteractionsPerTurn=9",
        "exec:java",
    ]


def test_build_bridge_launch_args_for_replay_with_table_id() -> None:
    launch_args = bridge_transport.build_bridge_launch_args(
        server="localhost",
        port=17171,
        username="Replay",
        table_id="table-123",
        error_log_path=Path("/tmp/game-002/Replay_errors.log"),
    )

    assert launch_args.jvm_args == (
        "--add-opens=java.base/java.io=ALL-UNNAMED "
        "-Dxmage.bridge.server=localhost "
        "-Dxmage.bridge.port=17171 "
        "-Dxmage.bridge.tableId=table-123"
    )
    assert launch_args.mvn_args == [
        "-q",
        "-Dxmage.bridge.username=Replay",
        "-Dxmage.bridge.errorlog=/tmp/game-002/Replay_errors.log",
        "exec:java",
    ]


def test_build_bridge_launch_args_adds_darwin_ui_flag(monkeypatch) -> None:
    monkeypatch.setattr(bridge_transport.sys, "platform", "darwin")

    launch_args = bridge_transport.build_bridge_launch_args(
        server="localhost",
        port=17171,
        username="MacPilot",
    )

    assert launch_args.jvm_args == (
        "--add-opens=java.base/java.io=ALL-UNNAMED "
        "-Dxmage.bridge.server=localhost "
        "-Dxmage.bridge.port=17171 "
        "-Dapple.awt.UIElement=true"
    )


async def test_spawn_bridge_uses_mcp_two_stream_transport(monkeypatch, tmp_path) -> None:
    """Exercise SDK client creation and the MCP 2 transport's two-stream contract."""
    reservation = MagicMock(port=19042)
    process = MagicMock()
    read, write = object(), object()
    session = object()

    @asynccontextmanager
    async def transport(url, *, http_client):
        assert url == "http://127.0.0.1:19042/mcp"
        assert isinstance(http_client, httpx2.AsyncClient)
        assert http_client.timeout.read is None
        assert http_client.timeout.connect == 30.0
        yield read, write

    @asynccontextmanager
    async def client_session(read_stream, write_stream):
        assert (read_stream, write_stream) == (read, write)
        yield session

    monkeypatch.setattr(bridge_transport, "find_available_port", lambda _: reservation)
    monkeypatch.setattr(bridge_transport.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(bridge_transport, "wait_for_port", lambda *_args: True)
    monkeypatch.setattr(bridge_transport, "streamable_http_client", transport)
    monkeypatch.setattr(bridge_transport, "ClientSession", client_session)

    async with bridge_transport.spawn_bridge_http(mvn_args=[], project_root=tmp_path, jvm_args="") as actual:
        assert actual is session

    process.stdin.close.assert_called_once()
    process.wait.assert_called_once_with(timeout=10)
