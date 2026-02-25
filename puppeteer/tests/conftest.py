"""Shared fixtures for golden prompt integration tests."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from puppeteer.orchestrator import compile_project
from puppeteer.port import find_available_port, wait_for_port
from puppeteer.process_manager import kill_tree
from puppeteer.xml_config import modify_server_config
from tests.golden_helpers import DECK_GOBLINS, DECK_RED_STOMPY, BridgeSession, PotatoProcess

_SET_CODE_RE = re.compile(r"\[([A-Z0-9]+):")


def extract_golden_set_codes(project_root: Path) -> str:
    """Extract set codes from all golden test deck files, returned as comma-separated string."""
    codes: set[str] = set()
    # Custom test decks
    for dck in (project_root / "puppeteer" / "tests" / "decks").glob("*.dck"):
        for match in _SET_CODE_RE.finditer(dck.read_text()):
            codes.add(match.group(1))
    # Legacy sample decks used by golden tests
    for legacy_path in [DECK_RED_STOMPY, DECK_GOBLINS]:
        path = project_root / legacy_path
        if path.exists():
            for match in _SET_CODE_RE.finditer(path.read_text()):
                codes.add(match.group(1))
    return ",".join(sorted(codes))


def pytest_collection_modifyitems(items: list) -> None:
    """Schedule tests that use session-scoped persistent JVM fixtures last.

    Tests that spawn their own subprocesses (e.g. two-replay tests) must run
    before the persistent bridge/potato fixtures are created, because those
    fixtures stay connected to the XMage server as "TestPlayer"/"Opponent" and
    their leftover table state can interfere with fresh subprocess clients
    that use the same usernames.
    """
    persistent_fixture_names = {"bridge_session", "potato_process"}
    non_persistent = []
    persistent = []
    for item in items:
        if persistent_fixture_names & set(item.fixturenames):
            persistent.append(item)
        else:
            non_persistent.append(item)
    items[:] = non_persistent + persistent


@pytest.fixture(scope="session")
def project_root():
    """Project root directory."""
    return Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="session")
def xmage_server(project_root, tmp_path_factory):
    """Compile project and start XMage server for golden integration tests.

    Yields (server_host, port).

    Requires GOLDEN_INTEGRATION=1 environment variable. Skips otherwise,
    so `make test` (which runs all tests) doesn't trigger a slow server
    startup. Use `make test-golden` to run these tests explicitly.
    """
    if not os.environ.get("GOLDEN_INTEGRATION"):
        pytest.skip("Golden integration tests: run with 'make test-golden'")

    # Compile all needed modules
    assert compile_project(project_root, observer=True), "Compilation failed"

    # Find available port
    port_res = find_available_port("localhost", 17171)
    port = port_res.port

    # Generate server config — use repo-local tmp/ for easy access
    tmp_dir = project_root / "tmp" / "golden-server"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    config_path = tmp_dir / "server_config.xml"
    modify_server_config(
        source=project_root / "Mage.Server" / "config" / "config.xml",
        destination=config_path,
        port=port,
    )

    # Restrict card pool to only the sets used by golden test decks
    allowed_sets = extract_golden_set_codes(project_root)

    # Build JVM options (server has no GUI; clients need AWT for Swing)
    jvm_opts = " ".join(
        [
            "--add-opens=java.base/java.io=ALL-UNNAMED",
            "-Djava.awt.headless=true",
            f"-Dxmage.sets.allowed={allowed_sets}",
        ]
    )

    # Start server
    env = os.environ.copy()
    env.update(
        {
            "XMAGE_AI_PUPPETEER": "1",
            "XMAGE_AI_PUPPETEER_USER": "spectator",
            "XMAGE_AI_PUPPETEER_PASSWORD": "",
            "XMAGE_AI_PUPPETEER_SERVER": "localhost",
            "XMAGE_AI_PUPPETEER_PORT": str(port),
            "XMAGE_AI_PUPPETEER_DISABLE_WHATS_NEW": "1",
            "MAVEN_OPTS": f"{jvm_opts} -Dxmage.config.path={config_path}",
        }
    )

    server_log = tmp_dir / "server.log"
    server_log_fh = open(server_log, "w")
    server_proc = subprocess.Popen(
        ["mvn", "-q", "exec:java"],
        cwd=project_root / "Mage.Server",
        env=env,
        stdout=server_log_fh,
        stderr=subprocess.STDOUT,
    )

    try:
        assert wait_for_port("localhost", port, 90), f"XMage server failed to start within 90s — check {server_log}"
        port_res.release()
        yield "localhost", port
    finally:
        kill_tree(server_proc.pid)
        server_log_fh.close()


@pytest.fixture(scope="session")
def bridge_session(xmage_server, project_root):
    """Session-scoped bridge JVM with persistent MCP session.

    Starts a sleepwalker bridge client with keepAlive=true. Communication
    happens via direct JSON-RPC over stdin/stdout (no MCP SDK needed).
    """
    server, port = xmage_server

    tmp_dir = project_root / "tmp" / "golden-bridge"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    jvm_opens = "--add-opens=java.base/java.io=ALL-UNNAMED"
    jvm_no_ui = jvm_opens
    if sys.platform == "darwin":
        jvm_no_ui += " -Dapple.awt.UIElement=true"

    allowed_sets = extract_golden_set_codes(project_root)

    bridge_jvm = " ".join(
        [
            jvm_no_ui,
            f"-Dxmage.bridge.server={server}",
            f"-Dxmage.bridge.port={port}",
            "-Dxmage.bridge.personality=sleepwalker",
            "-Dxmage.bridge.keepAlive=true",
            f"-Dxmage.sets.allowed={allowed_sets}",
        ]
    )

    bridge_log = tmp_dir / "bridge.log"
    bridge_log_fh = open(bridge_log, "w")

    proc = subprocess.Popen(
        ["mvn", "-q", "-Dxmage.bridge.username=TestPlayer", "exec:java"],
        cwd=project_root / "Mage.Client.Bridge",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=bridge_log_fh,
        env={**os.environ, "MAVEN_OPTS": bridge_jvm},
    )

    bridge = BridgeSession(proc)
    bridge.initialize()

    yield bridge

    bridge.close()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
    bridge_log_fh.close()


@pytest.fixture(scope="session")
def potato_process(xmage_server, project_root):
    """Session-scoped potato JVM controlled via stdin line protocol.

    Starts a potato bridge client with keepAlive=true. Each line written
    to stdin is a deck path — the potato loads it, resets state, joins the
    next available table, and plays the game.
    """
    server, port = xmage_server

    tmp_dir = project_root / "tmp" / "golden-potato"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    jvm_opens = "--add-opens=java.base/java.io=ALL-UNNAMED"
    jvm_no_ui = jvm_opens
    if sys.platform == "darwin":
        jvm_no_ui += " -Dapple.awt.UIElement=true"

    allowed_sets = extract_golden_set_codes(project_root)

    potato_jvm = " ".join(
        [
            jvm_no_ui,
            f"-Dxmage.bridge.server={server}",
            f"-Dxmage.bridge.port={port}",
            "-Dxmage.bridge.personality=potato",
            "-Dxmage.bridge.keepAlive=true",
            f"-Dxmage.sets.allowed={allowed_sets}",
        ]
    )

    potato_log = tmp_dir / "potato.log"
    potato_log_fh = open(potato_log, "w")

    proc = subprocess.Popen(
        ["mvn", "-q", "-Dxmage.bridge.username=Opponent", "exec:java"],
        cwd=project_root / "Mage.Client.Bridge",
        stdin=subprocess.PIPE,
        stdout=potato_log_fh,
        stderr=subprocess.STDOUT,
        env={**os.environ, "MAVEN_OPTS": potato_jvm},
    )

    potato = PotatoProcess(proc)

    yield potato

    potato.close()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
    potato_log_fh.close()
