"""Shared fixtures for golden prompt integration tests."""

from __future__ import annotations

import concurrent.futures
import dataclasses
import os
import re
import subprocess
from pathlib import Path

import pytest

from puppeteer.orchestrator import compile_project
from puppeteer.port import find_available_port, wait_for_port
from puppeteer.process_manager import kill_tree
from puppeteer.xml_config import modify_server_config
from tests.golden_helpers import (
    DECK_GOBLINS,
    DECK_RED_STOMPY,
    MAIN_CLASS_BRIDGE,
    MAIN_CLASS_OBSERVER,
    MAIN_CLASS_SERVER,
    BridgeSession,
    PotatoProcess,
    SpectatorProcess,
    _build_java_cmd,
    _wait_for_log_marker,
    compute_module_classpath,
    print_timing_summary,
    run_golden_scenario,
    run_golden_scenario_two_replay,
    timed_phase,
)

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
    """Schedule tests that use bridge/potato session fixtures last.

    Tests that spawn their own subprocesses (e.g. two-replay tests) must run
    before the persistent bridge/potato fixtures are created, because those
    fixtures stay connected to the XMage server as "TestPlayer"/"Opponent" and
    their leftover table state can interfere with fresh subprocess clients
    that use the same usernames.

    Note: spectator_process is NOT included here — the spectator connects as
    "spectator" (not a player username) so it doesn't conflict with subprocess
    clients.
    """
    bridge_potato_fixtures = {"bridge_session", "potato_process"}
    non_bridge_potato = []
    bridge_potato = []
    for item in items:
        if bridge_potato_fixtures & set(item.fixturenames):
            bridge_potato.append(item)
        else:
            non_bridge_potato.append(item)
    items[:] = non_bridge_potato + bridge_potato


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
    with timed_phase("session", "compilation"):
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

    # Build java -cp command (server has no GUI; clients need AWT for Swing)
    server_cp = compute_module_classpath(project_root, "Mage.Server")
    server_cmd = _build_java_cmd(
        server_cp,
        MAIN_CLASS_SERVER,
        {
            "java.awt.headless": "true",
            "xmage.sets.allowed": allowed_sets,
            "xmage.config.path": str(config_path),
        },
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
        }
    )

    server_log = tmp_dir / "server.log"
    server_log_fh = open(server_log, "w")
    server_proc = subprocess.Popen(
        server_cmd,
        cwd=project_root / "Mage.Server",
        env=env,
        stdout=server_log_fh,
        stderr=subprocess.STDOUT,
    )

    try:
        with timed_phase("session", "server_startup"):
            assert wait_for_port("localhost", port, 90), f"XMage server failed to start within 90s — check {server_log}"
        port_res.release()
        yield "localhost", port
    finally:
        kill_tree(server_proc.pid)
        server_log_fh.close()


@dataclasses.dataclass
class ScenarioResult:
    """Result of running a subprocess golden scenario."""

    prompt: list[dict]
    game_dir: Path
    error: BaseException | None


@pytest.fixture(scope="session")
def parallel_subprocess_results(
    xmage_server: tuple[str, int],
    project_root: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, ScenarioResult]:
    """Run all non-persistent golden scenarios in parallel.

    Each scenario uses unique XMage usernames so they can share the same
    server without collisions.  Results are collected and returned for
    individual test functions to assert against.
    """
    from tests.golden_scenarios import SUBPROCESS_SCENARIOS, Scenario

    server, port = xmage_server
    base_dir = tmp_path_factory.mktemp("parallel")

    def run_one(scenario: Scenario) -> tuple[str, ScenarioResult]:
        game_dir = base_dir / scenario.golden_name
        game_dir.mkdir()
        try:
            if scenario.script_b is not None:
                prompt = run_golden_scenario_two_replay(
                    server=server,
                    port=port,
                    project_root=project_root,
                    game_dir=game_dir,
                    deck_a=scenario.deck_a,
                    deck_b=scenario.deck_b,
                    script_a=scenario.script_a,
                    script_b=scenario.script_b,
                    golden_name=scenario.golden_name,
                    player_a_name=scenario.player_a_name,
                    player_b_name=scenario.player_b_name,
                    spectator_name=scenario.spectator_name,
                    skip_assert=True,
                )
            else:
                prompt = run_golden_scenario(
                    server=server,
                    port=port,
                    project_root=project_root,
                    game_dir=game_dir,
                    deck_a=scenario.deck_a,
                    deck_b=scenario.deck_b,
                    script=scenario.script_a,
                    golden_name=scenario.golden_name,
                    player_a_name=scenario.player_a_name,
                    player_b_name=scenario.player_b_name,
                    spectator_name=scenario.spectator_name,
                    skip_assert=True,
                )
            return scenario.golden_name, ScenarioResult(prompt, game_dir, None)
        except BaseException as e:
            return scenario.golden_name, ScenarioResult([], game_dir, e)

    results: dict[str, ScenarioResult] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(run_one, s) for s in SUBPROCESS_SCENARIOS]
        for future in concurrent.futures.as_completed(futures):
            name, result = future.result()
            results[name] = result

    return results


@pytest.fixture(scope="session")
def bridge_session(xmage_server, project_root):
    """Session-scoped bridge JVM with persistent MCP session.

    Starts a sleepwalker bridge client with keepAlive=true. Communication
    happens via JSON-RPC over HTTP.
    """
    server, port = xmage_server

    tmp_dir = project_root / "tmp" / "golden-bridge"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    allowed_sets = extract_golden_set_codes(project_root)

    # Allocate a port for the MCP HTTP server
    mcp_port_res = find_available_port("localhost", 19000)
    mcp_port = mcp_port_res.port

    bridge_cp = compute_module_classpath(project_root, "Mage.Client.Bridge")
    bridge_cmd = _build_java_cmd(
        bridge_cp,
        MAIN_CLASS_BRIDGE,
        {
            "xmage.bridge.server": server,
            "xmage.bridge.port": str(port),
            "xmage.bridge.personality": "sleepwalker",
            "xmage.bridge.keepAlive": "true",
            "xmage.bridge.mcpPort": str(mcp_port),
            "xmage.bridge.username": "TestPlayer",
            "xmage.sets.allowed": allowed_sets,
        },
    )

    bridge_log = tmp_dir / "bridge.log"
    bridge_log_fh = open(bridge_log, "w")

    proc = subprocess.Popen(
        bridge_cmd,
        cwd=project_root / "Mage.Client.Bridge",
        stdin=subprocess.PIPE,
        stdout=bridge_log_fh,
        stderr=subprocess.STDOUT,
    )

    with timed_phase("session", "bridge_jvm_startup"):
        print(f"Bridge JVM started (pid={proc.pid}), waiting for MCP HTTP server on port {mcp_port}...")
        assert wait_for_port("127.0.0.1", mcp_port, 120), (
            f"Bridge MCP HTTP server did not start on port {mcp_port} within 120s — check {bridge_log}"
        )
        mcp_port_res.release()

        bridge = BridgeSession(f"http://127.0.0.1:{mcp_port}/mcp")
        bridge.initialize()
        print("Bridge MCP initialized via HTTP")

    yield bridge

    bridge.close()
    # Close stdin to signal the bridge to shut down
    if proc.stdin:
        try:
            proc.stdin.close()
        except Exception:
            pass
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

    allowed_sets = extract_golden_set_codes(project_root)

    potato_cp = compute_module_classpath(project_root, "Mage.Client.Bridge")
    potato_cmd = _build_java_cmd(
        potato_cp,
        MAIN_CLASS_BRIDGE,
        {
            "xmage.bridge.server": server,
            "xmage.bridge.port": str(port),
            "xmage.bridge.personality": "potato",
            "xmage.bridge.keepAlive": "true",
            "xmage.bridge.username": "Opponent",
            "xmage.sets.allowed": allowed_sets,
        },
    )

    potato_log = tmp_dir / "potato.log"
    potato_log_fh = open(potato_log, "w")

    proc = subprocess.Popen(
        potato_cmd,
        cwd=project_root / "Mage.Client.Bridge",
        stdin=subprocess.PIPE,
        stdout=potato_log_fh,
        stderr=subprocess.STDOUT,
    )

    with timed_phase("session", "potato_jvm_startup"):
        potato = PotatoProcess(proc)
        print(f"Potato JVM started (pid={proc.pid})")

    yield potato

    potato.close()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
    potato_log_fh.close()


@pytest.fixture(scope="session")
def spectator_process(xmage_server, project_root):
    """Session-scoped observer spectator JVM with stdin command protocol.

    Starts the spectator with keepAlive=true. Each test sends a JSON command
    via stdin to create a new game table, avoiding the cost of spawning a
    fresh JVM per test.
    """
    server, port = xmage_server

    tmp_dir = project_root / "tmp" / "golden-spectator"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    allowed_sets = extract_golden_set_codes(project_root)

    cp = compute_module_classpath(project_root, "Mage.Client.Observer")
    spectator_cmd = _build_java_cmd(
        cp,
        MAIN_CLASS_OBSERVER,
        {
            "xmage.aiPuppeteer.autoConnect": "true",
            "xmage.aiPuppeteer.disableWhatsNew": "true",
            "xmage.observer.noWindow": "true",
            "xmage.observer.keepAlive": "true",
            "xmage.aiPuppeteer.server": server,
            "xmage.aiPuppeteer.port": str(port),
            "xmage.aiPuppeteer.user": "spectator",
            "xmage.aiPuppeteer.password": "",
            "xmage.sets.allowed": allowed_sets,
        },
    )

    spectator_log = tmp_dir / "spectator.log"
    spectator_log_fh = open(spectator_log, "w")

    env = os.environ.copy()
    env.update(
        {
            "XMAGE_AI_PUPPETEER": "1",
            "XMAGE_AI_PUPPETEER_USER": "spectator",
            "XMAGE_AI_PUPPETEER_PASSWORD": "",
            "XMAGE_AI_PUPPETEER_SERVER": server,
            "XMAGE_AI_PUPPETEER_PORT": str(port),
            "XMAGE_AI_PUPPETEER_DISABLE_WHATS_NEW": "1",
            "XMAGE_AI_PUPPETEER_SKIP_INIT_SHUFFLING": "true",
            "XMAGE_AI_PUPPETEER_WINS_NEEDED": "1",
        }
    )

    proc = subprocess.Popen(
        spectator_cmd,
        cwd=project_root / "Mage.Client.Observer",
        stdin=subprocess.PIPE,
        stdout=spectator_log_fh,
        stderr=subprocess.STDOUT,
        env=env,
    )

    with timed_phase("session", "spectator_jvm_startup"):
        spectator = SpectatorProcess(proc, spectator_log)
        print(f"Spectator JVM started (pid={proc.pid}), waiting for keepAlive ready...")
        _wait_for_log_marker(spectator_log, "keepAlive: lobby initialized, ready for commands", proc, timeout=120)
        print("Spectator keepAlive ready")

    yield spectator

    spectator.close()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
    spectator_log_fh.close()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Print aggregate golden test timing summary at session end."""
    print_timing_summary()
