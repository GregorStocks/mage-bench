"""Tests for shared bridge launch argument assembly."""

from pathlib import Path

from puppeteer import bridge_transport


def test_build_bridge_launch_args_for_sleepwalker() -> None:
    launch_args = bridge_transport.build_bridge_launch_args(
        server="example.org",
        port=17171,
        username="Sleeper",
        personality="sleepwalker",
        deck_path=Path("/tmp/decks/sleeper.dck"),
        heap_size_mb=512,
    )

    assert launch_args.jvm_args == (
        "--add-opens=java.base/java.io=ALL-UNNAMED "
        "-Xmx512m "
        "-Dxmage.bridge.server=example.org "
        "-Dxmage.bridge.port=17171 "
        "-Dxmage.bridge.personality=sleepwalker"
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
        personality="sleepwalker",
        deck_path=Path("/tmp/decks/pilot.dck"),
        heap_size_mb=512,
        error_log_path=game_dir / "Pilot_errors.log",
        bridge_log_path=game_dir / "Pilot_bridge.jsonl",
        max_interactions_per_turn=9,
    )

    assert launch_args.jvm_args == (
        "--add-opens=java.base/java.io=ALL-UNNAMED "
        "-Xmx512m "
        "-Dxmage.bridge.server=localhost "
        "-Dxmage.bridge.port=17171 "
        "-Dxmage.bridge.personality=sleepwalker"
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
        personality="sleepwalker",
        table_id="table-123",
        error_log_path=Path("/tmp/game-002/Replay_errors.log"),
    )

    assert launch_args.jvm_args == (
        "--add-opens=java.base/java.io=ALL-UNNAMED "
        "-Dxmage.bridge.server=localhost "
        "-Dxmage.bridge.port=17171 "
        "-Dxmage.bridge.personality=sleepwalker "
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
        personality="sleepwalker",
    )

    assert launch_args.jvm_args == (
        "--add-opens=java.base/java.io=ALL-UNNAMED "
        "-Dxmage.bridge.server=localhost "
        "-Dxmage.bridge.port=17171 "
        "-Dxmage.bridge.personality=sleepwalker "
        "-Dapple.awt.UIElement=true"
    )
