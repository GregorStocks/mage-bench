"""Shared fixtures and hooks for puppeteer tests."""

import gzip
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Generator, Iterator, Mapping
from pathlib import Path

import jsonschema
import pytest

from puppeteer.orchestrator import compile_project
from puppeteer.port import find_available_port, wait_for_port
from puppeteer.process_manager import jvm_oom_preexec_fn, kill_tree
from puppeteer.xml_config import modify_server_config
from tests.golden_fail_fast import GoldenFailureGate
from tests.golden_helpers import (
    DECK_GOBLINS,
    DECK_RED_STOMPY,
    MAIN_CLASS_OBSERVER,
    MAIN_CLASS_SERVER,
    BridgeManager,
    SpectatorProcess,
    _build_java_cmd,
    _wait_for_commands,
    _wait_for_health,
    compute_module_classpath,
    print_rss_summary,
    print_timing_summary,
    record_registered_rss_snapshot,
    register_observed_process,
    timed_phase,
    unregister_observed_process,
)

_SET_CODE_RE = re.compile(r"\[([A-Z0-9]+):")
_GOLDEN_FAILURE_GATE_KEY: pytest.StashKey[GoldenFailureGate] = pytest.StashKey()


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
        assert compile_project(project_root, observer=True, populate_local_repo=True), "Compilation failed"

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
    with timed_phase("session", "server_classpath"):
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
        preexec_fn=jvm_oom_preexec_fn(),
    )

    try:
        with timed_phase("session", "server_startup"):
            assert wait_for_port("localhost", port, 90), f"XMage server failed to start within 90s — check {server_log}"
        port_res.release()
        register_observed_process("server", server_proc.pid)
        record_registered_rss_snapshot("server_ready", ["server"])
        yield "localhost", port
    finally:
        unregister_observed_process("server")
        kill_tree(server_proc.pid)
        server_log_fh.close()


@pytest.fixture(scope="session")
def bridge_session(xmage_server, project_root):
    """Session-scoped bridge JVM with persistent MCP session.

    Starts a sleepwalker bridge client with keepAlive=true. Communication
    happens via JSON-RPC over HTTP. Automatically restarts if the bridge
    becomes unresponsive between tests (e.g. stuck mid-game after a failure).
    """
    server, port = xmage_server
    allowed_sets = extract_golden_set_codes(project_root)

    mgr = BridgeManager(server, port, project_root, allowed_sets)
    with timed_phase("session", "bridge_jvm_startup"):
        mgr.start()

    yield mgr

    mgr.stop()


@pytest.fixture(scope="session")
def opponent_session(xmage_server, project_root):
    """Session-scoped opponent bridge JVM with persistent MCP session.

    Starts a sleepwalker bridge client as "Opponent" with keepAlive=true.
    Runs replay scripts for the opponent side of golden tests.
    """
    server, port = xmage_server
    allowed_sets = extract_golden_set_codes(project_root)

    mgr = BridgeManager(server, port, project_root, allowed_sets, username="Opponent", label="opponent")
    with timed_phase("session", "opponent_jvm_startup"):
        mgr.start()

    yield mgr

    mgr.stop()


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

    # Allocate a port for the observer health HTTP server
    health_port_res = find_available_port("localhost", 20000)
    health_port = health_port_res.port

    with timed_phase("session", "spectator_classpath"):
        cp = compute_module_classpath(project_root, "Mage.Client.Observer")
    spectator_cmd = _build_java_cmd(
        cp,
        MAIN_CLASS_OBSERVER,
        {
            "xmage.aiPuppeteer.autoConnect": "true",
            "xmage.aiPuppeteer.disableWhatsNew": "true",
            "xmage.observer.noWindow": "true",
            "xmage.observer.keepAlive": "true",
            "xmage.observer.healthPort": str(health_port),
            "xmage.aiPuppeteer.server": server,
            "xmage.aiPuppeteer.port": str(port),
            "xmage.aiPuppeteer.user": "spectator",
            "xmage.aiPuppeteer.password": "",
            "xmage.sets.allowed": allowed_sets,
        },
    )

    # The observer needs a display for Swing (JFrame) even in noWindow mode.
    # On headless Linux, wrap with xvfb-run like the orchestrator does.
    if sys.platform == "linux" and "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ:
        xvfb = shutil.which("xvfb-run")
        assert xvfb is not None, (
            "Headless environment detected (no DISPLAY set) but xvfb-run is not installed. "
            "Install xvfb for your distribution (e.g. apt-get install xvfb or dnf install xorg-x11-server-Xvfb)."
        )
        spectator_cmd = [xvfb, "--auto-servernum", "--server-args=-screen 0 1920x1080x24", *spectator_cmd]

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
        preexec_fn=jvm_oom_preexec_fn(),
    )

    with timed_phase("session", "spectator_jvm_startup"):
        spectator = SpectatorProcess(proc, spectator_log, health_port=health_port)
        print(f"Spectator JVM started (pid={proc.pid}), waiting for health endpoint on port {health_port}...")
        assert wait_for_port("127.0.0.1", health_port, 120), (
            f"Observer health server did not start on port {health_port} within 120s — check {spectator_log}"
        )
        health_port_res.release()
        _wait_for_commands(health_port, timeout=120)
        _wait_for_health(health_port, timeout=120)
        print("Spectator keepAlive ready")
        register_observed_process("spectator", proc.pid)
        record_registered_rss_snapshot("spectator_ready", ["spectator"])

    yield spectator

    unregister_observed_process("spectator")
    spectator.close()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
    spectator_log_fh.close()


# ---------------------------------------------------------------------------
# Session-scoped fixtures for game export tests (shared across test files)
# ---------------------------------------------------------------------------


def _glob_game_files() -> list[Path]:
    """Find all game export files, preferring .json.gz over .json."""
    games_dir = Path(__file__).resolve().parent.parent.parent / "website" / "public" / "games"
    gz_files = set(games_dir.glob("game_*.json.gz"))
    gz_stems = {p.name.removesuffix(".gz") for p in gz_files}
    json_files = [p for p in games_dir.glob("game_*.json") if p.name not in gz_stems]
    return sorted(gz_files | set(json_files))


def _load_game(path: Path) -> dict:
    if path.suffix == ".gz":
        with gzip.open(path, "rt") as f:
            return json.load(f)
    with open(path) as f:
        return json.load(f)


class _LazyGameData(Mapping[Path, dict]):
    """Mapping that loads game JSON lazily on first access per key.

    Keys are populated eagerly (all game file paths), but values are only
    parsed from disk when first accessed.  This avoids the ~11s upfront cost
    of loading all 298 exports when only a few are actually needed.
    """

    def __init__(self, paths: list[Path]):
        self._paths = paths
        self._path_set = frozenset(paths)
        self._data: dict[Path, dict] = {}

    def __getitem__(self, key: Path) -> dict:
        if key not in self._data:
            if key not in self._path_set:
                raise KeyError(key)
            self._data[key] = _load_game(key)
        return self._data[key]

    def __iter__(self) -> Iterator[Path]:
        return iter(self._paths)

    def __len__(self) -> int:
        return len(self._paths)


@pytest.fixture(scope="session")
def all_games_data() -> Mapping[Path, dict]:
    """Lazy-loading map of game export files, parsed on first access."""
    return _LazyGameData(_glob_game_files())


@pytest.fixture(scope="session")
def game_export_validator():
    """Per-version game-export JSON Schema validators keyed by version number."""
    schema_dir = Path(__file__).resolve().parent.parent.parent / "schemas"
    validators = {}
    for path in sorted(schema_dir.glob("game-export-v*.schema.json")):
        schema = json.loads(path.read_text())
        version = schema["properties"]["version"]["const"]
        validators[version] = jsonschema.Draft7Validator(schema)
    assert validators, "No game-export schemas found"
    return validators


def _golden_failure_gate(config: pytest.Config) -> GoldenFailureGate:
    gate = config.stash.get(_GOLDEN_FAILURE_GATE_KEY, None)
    if gate is not None:
        return gate
    gate = GoldenFailureGate()
    config.stash[_GOLDEN_FAILURE_GATE_KEY] = gate
    return gate


def pytest_runtest_setup(item: pytest.Item) -> None:
    reason = _golden_failure_gate(item.config).skip_reason_for(
        item.nodeid,
        is_golden=item.get_closest_marker("golden") is not None,
    )
    if reason is not None:
        pytest.skip(reason)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Generator[None, object, object]:
    report = yield
    if item.get_closest_marker("golden") is None:
        return report
    if report.failed and not getattr(report, "wasxfail", False):
        _golden_failure_gate(item.config).record_failure(item.nodeid, report.when)
    return report


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Print aggregate golden test timing summary at session end."""
    print_timing_summary()
    print_rss_summary()
