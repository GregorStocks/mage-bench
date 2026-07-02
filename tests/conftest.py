"""Shared fixtures and hooks for Python tests."""

import dataclasses
import gzip
import json
import os
import re
import subprocess
import time
from collections.abc import Generator, Iterator, Mapping
from pathlib import Path

import fastjsonschema
import pytest

from magebench.common.json5_utils import loads_json5
from magebench.common.port import find_available_port, wait_for_port
from magebench.common.process_manager import jvm_oom_preexec_fn, kill_tree
from magebench.orchestration.orchestrator import compile_project
from magebench.orchestration.xml_config import modify_server_config
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
    read_health_port_file,
    record_registered_rss_snapshot,
    register_observed_process,
    timed_phase,
    unregister_observed_process,
    wrap_with_xvfb,
)
from tests.golden_test_identities import (
    GoldenTestIdentity,
    get_golden_test_identity,
    validate_golden_test_identities,
)

_SET_CODE_RE = re.compile(r"\[([A-Z0-9]+):")
_GOLDEN_FAILURE_GATE_KEY: pytest.StashKey[GoldenFailureGate] = pytest.StashKey()
_SERVER_INFO_FILENAME = "shared_server.json"

# Tests must never depend on live Scryfall responses (token searches order by
# release date, so a new printing would change golden exports). Point the
# scryfall module at the repo-committed fixture and forbid network fetches; a
# cache miss raises with instructions for extending the fixture.
_SCRYFALL_FIXTURE = Path(__file__).parent / "golden" / "scryfall-cache.json"
assert _SCRYFALL_FIXTURE.exists(), f"Missing scryfall fixture: {_SCRYFALL_FIXTURE}"
os.environ["MAGEBENCH_SCRYFALL_CACHE"] = str(_SCRYFALL_FIXTURE)
os.environ.setdefault("MAGEBENCH_SCRYFALL_OFFLINE", "1")


@dataclasses.dataclass(frozen=True)
class _SharedXmageServerInfo:
    host: str
    port: int
    pid: int


@dataclasses.dataclass
class _OwnedXmageServer:
    info: _SharedXmageServerInfo
    log_fh: object


_OWNED_XMAGE_SERVER: _OwnedXmageServer | None = None


def extract_golden_set_codes(project_root: Path) -> str:
    """Extract set codes from all golden test deck files, returned as comma-separated string."""
    codes: set[str] = set()
    # Custom test decks
    for dck in (project_root / "tests" / "decks").glob("*.dck"):
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
    return Path(__file__).resolve().parent.parent


def _is_xdist_worker(config: pytest.Config) -> bool:
    return hasattr(config, "workerinput")


def _xdist_worker_count(config: pytest.Config) -> int:
    numprocesses = getattr(config.option, "numprocesses", None)
    if numprocesses in (None, 0):
        return 0
    if numprocesses == "auto":
        return 1
    return int(numprocesses)


def _uses_shared_golden_server(config: pytest.Config) -> bool:
    return _xdist_worker_count(config) > 0


def _shared_server_info_path(project_root: Path) -> Path:
    return project_root / "tmp" / "golden-server" / _SERVER_INFO_FILENAME


def _read_shared_server_info(path: Path) -> _SharedXmageServerInfo:
    data = json.loads(path.read_text())
    return _SharedXmageServerInfo(
        host=data["host"],
        port=data["port"],
        pid=data["pid"],
    )


def _wait_for_shared_server_info(path: Path, timeout: float = 300.0) -> _SharedXmageServerInfo:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return _read_shared_server_info(path)
        except FileNotFoundError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"Shared XMage server info {path} was not written within {timeout:.1f}s")


def _start_xmage_server(project_root: Path) -> _OwnedXmageServer:
    # Compile all needed modules once before any worker launches clients.
    with timed_phase("session", "compilation"):
        assert compile_project(project_root, observer=True, populate_local_repo=True), "Compilation failed"

    port_res = find_available_port(17171)
    port = port_res.port

    tmp_dir = project_root / "tmp" / "golden-server"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    config_path = tmp_dir / "server_config.xml"
    modify_server_config(
        source=project_root / "Mage.Server" / "config" / "config.xml",
        destination=config_path,
        port=port,
    )

    allowed_sets = extract_golden_set_codes(project_root)

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
        max_heap="512m",
    )

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
    except Exception:
        kill_tree(server_proc.pid)
        server_log_fh.close()
        raise
    finally:
        port_res.release()

    info = _SharedXmageServerInfo(host="localhost", port=port, pid=server_proc.pid)
    register_observed_process("server", server_proc.pid)
    record_registered_rss_snapshot("server_ready", ["server"])
    return _OwnedXmageServer(info=info, log_fh=server_log_fh)


def _stop_xmage_server(server: _OwnedXmageServer) -> None:
    unregister_observed_process("server")
    kill_tree(server.info.pid)
    server.log_fh.close()


def _ensure_shared_xmage_server_started(project_root: Path) -> _SharedXmageServerInfo:
    global _OWNED_XMAGE_SERVER
    if _OWNED_XMAGE_SERVER is not None:
        return _OWNED_XMAGE_SERVER.info

    info_path = _shared_server_info_path(project_root)
    info_path.parent.mkdir(parents=True, exist_ok=True)
    info_path.unlink(missing_ok=True)
    owned = _start_xmage_server(project_root)
    info_path.write_text(json.dumps(dataclasses.asdict(owned.info), indent=2) + "\n")
    _OWNED_XMAGE_SERVER = owned
    return owned.info


def _stop_shared_xmage_server(project_root: Path) -> None:
    global _OWNED_XMAGE_SERVER
    if _OWNED_XMAGE_SERVER is None:
        return
    info_path = _shared_server_info_path(project_root)
    try:
        _stop_xmage_server(_OWNED_XMAGE_SERVER)
    finally:
        _OWNED_XMAGE_SERVER = None
        info_path.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def xmage_server(project_root, request: pytest.FixtureRequest):
    """Compile project and start XMage server for golden integration tests.

    Yields (server_host, port).

    Requires GOLDEN_INTEGRATION=1 environment variable. Skips otherwise,
    so `make test` (which runs all tests) doesn't trigger a slow server
    startup. Use `make test-golden` to run these tests explicitly.
    """
    if not os.environ.get("GOLDEN_INTEGRATION"):
        pytest.skip("Golden integration tests: run with 'make test-golden'")

    config = request.config
    if _is_xdist_worker(config):
        info = _wait_for_shared_server_info(_shared_server_info_path(project_root))
        register_observed_process("server", info.pid)
        try:
            yield info.host, info.port
        finally:
            unregister_observed_process("server")
        return

    if _uses_shared_golden_server(config):
        info = _ensure_shared_xmage_server_started(project_root)
        yield info.host, info.port
        return

    owned = _start_xmage_server(project_root)
    try:
        yield owned.info.host, owned.info.port
    finally:
        _stop_xmage_server(owned)


@pytest.fixture
def golden_identity(request: pytest.FixtureRequest) -> GoldenTestIdentity:
    """Per-test identity bundle for real golden integration tests."""
    identity = get_golden_test_identity(getattr(request.node, "obj", None))
    assert identity is not None, f"{request.node.nodeid} uses golden fixtures but is missing @golden_test(...)."
    return identity


@pytest.fixture
def bridge_session(xmage_server, project_root, golden_identity: GoldenTestIdentity):
    """Fresh bridge JVM for one golden test, sharing only the XMage server."""
    server, port = xmage_server

    mgr = BridgeManager(
        server,
        port,
        project_root,
        username=golden_identity.player_a_name,
        label=golden_identity.bridge_label,
    )
    with timed_phase(golden_identity.case_id, "bridge_jvm_startup"):
        mgr.start()

    yield mgr

    mgr.stop()


@pytest.fixture
def opponent_session(xmage_server, project_root, golden_identity: GoldenTestIdentity):
    """Fresh opponent bridge JVM for one golden test, sharing only the XMage server."""
    server, port = xmage_server

    mgr = BridgeManager(
        server,
        port,
        project_root,
        username=golden_identity.player_b_name,
        label=golden_identity.opponent_label,
    )
    with timed_phase(golden_identity.case_id, "opponent_jvm_startup"):
        mgr.start()

    yield mgr

    mgr.stop()


@pytest.fixture
def spectator_process(xmage_server, project_root, golden_identity: GoldenTestIdentity):
    """Fresh observer spectator JVM for one golden test, sharing only the XMage server."""
    server, port = xmage_server

    tmp_dir = project_root / "tmp" / f"golden-{golden_identity.spectator_label}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # Health port file: Java will bind with retry and write the actual port here
    health_port_file = tmp_dir / "health_port"
    health_port_file.unlink(missing_ok=True)

    with timed_phase(golden_identity.case_id, "spectator_classpath"):
        cp = compute_module_classpath(project_root, "Mage.Client.Observer")
    spectator_cmd = _build_java_cmd(
        cp,
        MAIN_CLASS_OBSERVER,
        {
            "xmage.aiPuppeteer.autoConnect": "true",
            "xmage.aiPuppeteer.disableWhatsNew": "true",
            "xmage.observer.noWindow": "true",
            "xmage.observer.keepAlive": "true",
            "xmage.observer.healthPort": "20000",
            "xmage.observer.healthPortFile": str(health_port_file),
            "xmage.aiPuppeteer.server": server,
            "xmage.aiPuppeteer.port": str(port),
            "xmage.aiPuppeteer.user": golden_identity.spectator_name,
            "xmage.aiPuppeteer.password": "",
        },
        max_heap="512m",
    )
    spectator_cmd = wrap_with_xvfb(spectator_cmd)

    spectator_log = tmp_dir / "spectator.log"
    spectator_log_fh = open(spectator_log, "w")

    env = os.environ.copy()
    env.update(
        {
            "XMAGE_AI_PUPPETEER": "1",
            "XMAGE_AI_PUPPETEER_USER": golden_identity.spectator_name,
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

    with timed_phase(golden_identity.case_id, "spectator_jvm_startup"):
        print(f"Spectator JVM started (pid={proc.pid}), waiting for health port file...")
        health_port = read_health_port_file(health_port_file, timeout=120)
        print(f"Observer health server bound to port {health_port}")
        spectator = SpectatorProcess(
            proc,
            spectator_log,
            health_port=health_port,
            label=golden_identity.spectator_label,
        )
        _wait_for_commands(health_port, timeout=120)
        _wait_for_health(health_port, timeout=120)
        print("Spectator keepAlive ready")
        register_observed_process(golden_identity.spectator_label, proc.pid)
        record_registered_rss_snapshot(
            f"{golden_identity.case_id}_spectator_ready",
            [golden_identity.spectator_label],
        )

    yield spectator

    unregister_observed_process(golden_identity.spectator_label)
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
    """Find all game export files, preferring .json5.gz over .json5."""
    games_dir = Path(__file__).resolve().parent.parent / "website" / "public" / "games"
    gz_files = set(games_dir.glob("game_*.json5.gz"))
    gz_stems = {p.name.removesuffix(".gz") for p in gz_files}
    json_files = [p for p in games_dir.glob("game_*.json5") if p.name not in gz_stems]
    return sorted(gz_files | set(json_files))


def _load_game(path: Path) -> dict:
    if path.suffix == ".gz":
        with gzip.open(path, "rt") as f:
            return loads_json5(f.read())
    return loads_json5(path.read_text())


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
    """Per-version compiled game-export JSON Schema validators keyed by version number."""
    schema_dir = Path(__file__).resolve().parent.parent / "src" / "magebench" / "game"
    validators = {}
    for path in sorted(schema_dir.glob("game-export-v*.schema.json")):
        schema = json.loads(path.read_text())
        version = schema["properties"]["version"]["const"]
        validators[version] = fastjsonschema.compile(schema)
    assert validators, "No game-export schemas found"
    return validators


def _golden_failure_gate(config: pytest.Config) -> GoldenFailureGate:
    gate = config.stash.get(_GOLDEN_FAILURE_GATE_KEY, None)
    if gate is not None:
        return gate
    gate = GoldenFailureGate()
    config.stash[_GOLDEN_FAILURE_GATE_KEY] = gate
    return gate


def pytest_configure(config: pytest.Config) -> None:
    if not os.environ.get("GOLDEN_INTEGRATION"):
        return
    if _is_xdist_worker(config):
        return
    if not _uses_shared_golden_server(config):
        return
    project_root = Path(__file__).resolve().parent.parent
    _ensure_shared_xmage_server_started(project_root)


def pytest_unconfigure(config: pytest.Config) -> None:
    if not os.environ.get("GOLDEN_INTEGRATION"):
        return
    if _is_xdist_worker(config):
        return
    if not _uses_shared_golden_server(config):
        return
    project_root = Path(__file__).resolve().parent.parent
    _stop_shared_xmage_server(project_root)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    _ = config
    golden_cases: list[tuple[str, GoldenTestIdentity | None]] = []
    for item in items:
        if item.get_closest_marker("golden") is None:
            continue
        golden_cases.append((item.nodeid, get_golden_test_identity(getattr(item, "obj", None))))
    validate_golden_test_identities(golden_cases)


def pytest_runtest_setup(item: pytest.Item) -> None:
    reason = _golden_failure_gate(item.config).skip_reason_for(
        item.nodeid,
        is_golden=item.get_closest_marker("golden") is not None,
    )
    if reason is not None:
        pytest.skip(reason)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Generator[None, object, object]:
    _ = call
    report = yield
    if item.get_closest_marker("golden") is None:
        return report
    if report.failed and not getattr(report, "wasxfail", False):
        _golden_failure_gate(item.config).record_failure(item.nodeid, report.when)
    return report


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Print aggregate golden test timing summary at session end."""
    _ = session, exitstatus
    print_timing_summary()
    print_rss_summary()
