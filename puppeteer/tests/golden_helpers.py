"""Shared helpers for golden prompt integration tests.

Runs real XMage games, captures the exact production prompt messages that
``run_pilot_loop()`` would send to the LLM, and compares against golden files.

These are integration tests that require compilation and a running XMage server.
They are NOT included in ``make test`` — run them with ``make test-golden``.

To run:    make test-golden
To update: make regen-golden
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from collections.abc import Generator, Iterable, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import psutil

from puppeteer.config import load_prompts
from puppeteer.game_log import GameLogWriter
from puppeteer.harness_epoch import HARNESS_EPOCH
from puppeteer.pilot import DEFAULT_MODEL, mcp_tools_to_openai, run_pilot_loop
from puppeteer.port import find_available_port, wait_for_port
from puppeteer.process_manager import jvm_oom_preexec_fn, kill_tree
from puppeteer.replay import _is_meta_script_step, _run_meta_script_step, execute_replay_script
from schemas.game_export_types import Decision, json_default
from scripts.analysis.blunder_analysis import (
    _actions_by_turn,
    _collect_card_names,
    _game_overview,
    _get_oracle_texts,
    build_decision_prompt,
)
from scripts.analysis.blunder_eval_common import decision_index
from scripts.analysis.extract_decisions import extract_decisions
from scripts.export_game import build_export

# ---------------------------------------------------------------------------
# Timing instrumentation
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class PhaseTiming:
    """A single recorded phase timing."""

    test_name: str
    phase: str
    duration: float


_all_timings: list[PhaseTiming] = []


@dataclasses.dataclass
class RssSnapshot:
    """A single recorded process-tree RSS snapshot."""

    label: str
    total_rss_bytes: int
    process_rss_bytes: dict[str, int]


_rss_snapshots: list[RssSnapshot] = []
_observed_process_pids: dict[str, int] = {}


@contextmanager
def timed_phase(test_name: str, phase: str) -> Generator[None, None, None]:
    """Record wall-clock time for a named phase and print it in real-time."""
    t0 = time.monotonic()
    try:
        yield
    finally:
        duration = time.monotonic() - t0
        _all_timings.append(PhaseTiming(test_name, phase, duration))
        print(f"  [{test_name}/{phase}] {duration:.1f}s", flush=True)


def get_all_timings() -> list[PhaseTiming]:
    """Return all recorded timings (for testing)."""
    return list(_all_timings)


def clear_timings() -> None:
    """Clear all recorded timings (for testing)."""
    _all_timings.clear()


def get_rss_snapshots() -> list[RssSnapshot]:
    """Return all recorded RSS snapshots (for testing)."""
    return list(_rss_snapshots)


def clear_rss_snapshots() -> None:
    """Clear all recorded RSS snapshots (for testing)."""
    _rss_snapshots.clear()


def _format_rss_bytes(num_bytes: int) -> str:
    return f"{num_bytes / (1024 * 1024):.1f} MiB"


def _process_tree_rss_bytes(pid: int) -> int:
    """Return RSS for a process and all live descendants."""
    try:
        root = psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0

    total = 0
    for proc in [root, *root.children(recursive=True)]:
        try:
            total += proc.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total


def register_observed_process(label: str, pid: int) -> None:
    """Register a process tree for later RSS snapshots."""
    _observed_process_pids[label] = pid


def unregister_observed_process(label: str) -> None:
    """Stop tracking a previously registered process."""
    _observed_process_pids.pop(label, None)


def record_rss_snapshot(label: str, processes: Mapping[str, int]) -> None:
    """Capture and print process-tree RSS for the given processes."""
    if not processes:
        return

    process_rss_bytes: dict[str, int] = {}
    total_rss_bytes = 0
    for process_label, pid in processes.items():
        rss_bytes = _process_tree_rss_bytes(pid)
        process_rss_bytes[process_label] = rss_bytes
        total_rss_bytes += rss_bytes

    _rss_snapshots.append(RssSnapshot(label, total_rss_bytes, process_rss_bytes))
    breakdown = " ".join(
        f"{process_label}:{_format_rss_bytes(rss_bytes)}" for process_label, rss_bytes in process_rss_bytes.items()
    )
    print(f"  [rss/{label}] total={_format_rss_bytes(total_rss_bytes)} [{breakdown}]", flush=True)


def record_registered_rss_snapshot(label: str, process_labels: Iterable[str] | None = None) -> None:
    """Capture RSS for a subset of registered processes, preserving label order."""
    if process_labels is None:
        selected = dict(_observed_process_pids)
    else:
        selected = {
            process_label: _observed_process_pids[process_label]
            for process_label in process_labels
            if process_label in _observed_process_pids
        }
    record_rss_snapshot(label, selected)


def print_timing_summary() -> None:
    """Print an aggregate timing summary of all recorded phases."""
    if not _all_timings:
        return

    print("\n=== Golden Test Timing Summary ===\n", flush=True)

    # Session setup vs per-test
    session_timings = [t for t in _all_timings if t.test_name == "session"]
    test_timings = [t for t in _all_timings if t.test_name != "session"]

    # Session setup
    if session_timings:
        print("Session setup:", flush=True)
        setup_total = 0.0
        for t in session_timings:
            print(f"  {t.phase:<28s} {t.duration:>6.1f}s", flush=True)
            setup_total += t.duration
        print(f"  {'setup total':<28s} {setup_total:>6.1f}s", flush=True)
        print(flush=True)

    # Per-test breakdown
    if test_timings:
        # Group by test name, preserving order
        tests_seen: list[str] = []
        by_test: dict[str, list[PhaseTiming]] = defaultdict(list)
        for t in test_timings:
            if t.test_name not in tests_seen:
                tests_seen.append(t.test_name)
            by_test[t.test_name].append(t)

        print("Per-test breakdown:", flush=True)
        for test_name in tests_seen:
            phases = by_test[test_name]
            test_total = sum(p.duration for p in phases)
            phase_strs = [f"{p.phase}:{p.duration:.1f}" for p in phases]
            print(f"  {test_name:<32s} {test_total:>6.1f}s  [{' '.join(phase_strs)}]", flush=True)
        print(flush=True)

    # Aggregate
    total = sum(t.duration for t in _all_timings)
    minutes = int(total // 60)
    seconds = total % 60
    print(f"Aggregate ({minutes}m {seconds:.1f}s total):", flush=True)

    # Sum by phase across all tests
    by_phase: dict[str, float] = defaultdict(float)
    for t in _all_timings:
        by_phase[t.phase] += t.duration
    for phase, duration in sorted(by_phase.items(), key=lambda x: -x[1]):
        pct = (duration / total * 100) if total > 0 else 0
        print(f"  {phase:<28s} {duration:>6.1f}s  ({pct:>4.1f}%)", flush=True)
    print(flush=True)


def print_rss_summary() -> None:
    """Print all recorded RSS snapshots and the peak total RSS."""
    if not _rss_snapshots:
        return

    print("\n=== Golden Test RSS Summary ===\n", flush=True)
    peak_snapshot = max(_rss_snapshots, key=lambda snapshot: snapshot.total_rss_bytes)
    print(
        f"Peak total RSS: {_format_rss_bytes(peak_snapshot.total_rss_bytes)} at {peak_snapshot.label}",
        flush=True,
    )
    print("Snapshots:", flush=True)
    for snapshot in _rss_snapshots:
        breakdown = " ".join(
            f"{process_label}:{_format_rss_bytes(rss_bytes)}"
            for process_label, rss_bytes in snapshot.process_rss_bytes.items()
        )
        print(
            f"  {snapshot.label:<32s} {_format_rss_bytes(snapshot.total_rss_bytes):>10s}  [{breakdown}]",
            flush=True,
        )
    print(flush=True)


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "golden" / "prompts"
GOLDEN_EXPORTS_DIR = Path(__file__).resolve().parent / "golden" / "exports"
GOLDEN_BLUNDER_DIR = Path(__file__).resolve().parent / "golden" / "blunder_prompts"

UPDATE_MODE = os.environ.get("UPDATE_GOLDEN", "").lower() in ("1", "true", "yes")
SPECTATOR_READY_TIMEOUT_SECONDS = 240
# Must stay above BridgeCallbackHandler.KEEPALIVE_CONCEDE_WAIT_SECONDS so
# defensive cleanup doesn't time out while Java is still waiting for GAME_OVER.
DEFENSIVE_CONCEDE_TIMEOUT_SECONDS = 20

# Default decks for tests (relative to project root)
DECK_RED_STOMPY = "Mage.Client/release/sample-decks/Legacy/Red-Stompy.dck"
DECK_GOBLINS = "Mage.Client/release/sample-decks/Legacy/Goblins.dck"

# Custom test decks (relative to project root)
DECK_BOLT_AND_BURN = "puppeteer/tests/decks/bolt_and_burn.dck"
DECK_BLACK_LOTUS_DIVINATION = "puppeteer/tests/decks/black_lotus_divination.dck"
DECK_CLONE_AND_MEMNITE = "puppeteer/tests/decks/clone_and_memnite.dck"
DECK_DARK_DEPTHS_COMBO = "puppeteer/tests/decks/dark_depths_combo.dck"
DECK_EMANCIPATION_ANGEL = "puppeteer/tests/decks/emancipation_angel.dck"
DECK_FILLER = "puppeteer/tests/decks/filler_opponent.dck"
DECK_MANA_DRAIN_FOF = "puppeteer/tests/decks/mana_drain_fact_or_fiction.dck"
DECK_PLAINS_LIONS = "puppeteer/tests/decks/plains_lions_opponent.dck"
DECK_SAVANNAH_LIONS = "puppeteer/tests/decks/savannah_lions.dck"
DECK_ANCIENT_STIRRINGS = "puppeteer/tests/decks/ancient_stirrings.dck"
DECK_MDFC_LAND_AND_SUSPEND = "puppeteer/tests/decks/mdfc_land_and_suspend.dck"
DECK_GRIZZLY_BEARS = "puppeteer/tests/decks/grizzly_bears.dck"
DECK_TWO_MEMNITES = "puppeteer/tests/decks/two_savannah_lions.dck"


# Main classes for direct java -cp launches (from each module's pom.xml exec-maven-plugin config)
MAIN_CLASS_OBSERVER = "mage.client.observer.ObserverMain"
MAIN_CLASS_BRIDGE = "mage.client.bridge.BridgeClient"
MAIN_CLASS_SERVER = "mage.server.Main"

# ---------------------------------------------------------------------------
# Classpath computation (cached per module within a pytest session)
# ---------------------------------------------------------------------------

_classpath_cache: dict[str, str] = {}
_reactor_module_cache: dict[Path, dict[str, Path]] = {}


def _find_reactor_modules(project_root: Path) -> dict[str, Path]:
    """Map artifactId -> target/classes Path for all reactor modules.

    Walks the Maven reactor structure by following ``<module>`` declarations
    in pom.xml files.  Only includes modules that have a compiled
    ``target/classes`` directory.  Results are cached per *project_root*.
    """
    if project_root in _reactor_module_cache:
        return _reactor_module_cache[project_root]

    modules: dict[str, Path] = {}

    def _scan(parent_dir: Path) -> None:
        pom = parent_dir / "pom.xml"
        if not pom.exists():
            return
        content = pom.read_text()

        # Extract this module's artifactId (first <artifactId> after </parent>).
        parent_end = content.find("</parent>")
        search_text = content[parent_end:] if parent_end >= 0 else content
        m = re.search(r"<artifactId>([^<]+)</artifactId>", search_text)
        if m:
            classes_dir = parent_dir / "target" / "classes"
            if classes_dir.is_dir():
                modules[m.group(1)] = classes_dir

        # Recurse into child modules.
        for child in re.findall(r"<module>([^<]+)</module>", content):
            _scan(parent_dir / child)

    _scan(project_root)
    _reactor_module_cache[project_root] = modules
    return modules


def _replace_reactor_jars(dep_classpath: str, project_root: Path) -> str:
    """Replace ``~/.m2/repository`` JARs for reactor modules with ``target/classes``.

    Scans each colon-separated classpath entry for JARs under
    ``org/mage/<artifactId>/`` and swaps them for the module's compiled
    classes directory when available.
    """
    reactor = _find_reactor_modules(project_root)
    if not reactor:
        return dep_classpath

    entries = dep_classpath.split(":")
    resolved: list[str] = []
    for entry in entries:
        replaced = False
        for artifact_id, classes_dir in reactor.items():
            # Match ~/.m2/repository/org/mage/<artifactId>/<version>/<file>.jar
            if entry.endswith(".jar") and f"/org/mage/{artifact_id}/" in entry:
                resolved.append(str(classes_dir))
                replaced = True
                break
        if not replaced:
            resolved.append(entry)
    return ":".join(resolved)


def compute_module_classpath(project_root: Path, module: str) -> str:
    """Compute the Java classpath for a Maven module, cached per session.

    Runs ``mvn dependency:build-classpath`` on first call per module, then
    returns the cached result on subsequent calls. The classpath includes
    the module's own ``target/classes`` directory prepended to the dependency
    classpath.  Reactor module JARs from ``~/.m2/repository`` are replaced
    with their ``target/classes`` directories to avoid stale-JAR issues.
    """
    if module in _classpath_cache:
        return _classpath_cache[module]
    module_dir = project_root / module
    cp_file = module_dir / "target" / "classpath.txt"
    result = subprocess.run(
        ["mvn", "-q", "dependency:build-classpath", f"-Dmdep.outputFile={cp_file}"],
        cwd=module_dir,
        capture_output=True,
        text=True,
        preexec_fn=jvm_oom_preexec_fn(),
    )
    assert result.returncode == 0, f"Failed to compute classpath for {module}: {result.stderr}"
    dep_classpath = cp_file.read_text().strip()
    dep_classpath = _replace_reactor_jars(dep_classpath, project_root)
    classpath = f"{module_dir / 'target' / 'classes'}:{dep_classpath}"
    _classpath_cache[module] = classpath
    return classpath


def _build_java_cmd(
    classpath: str,
    main_class: str,
    system_props: dict[str, str],
    *,
    max_heap: str | None = None,
    max_metaspace: str | None = None,
) -> list[str]:
    """Build a ``java -cp`` command with JVM flags and system properties."""
    jvm_flags = ["--add-opens=java.base/java.io=ALL-UNNAMED"]
    if max_heap is not None:
        jvm_flags.append(f"-Xmx{max_heap}")
    if max_metaspace is not None:
        jvm_flags.append(f"-XX:MaxMetaspaceSize={max_metaspace}")
    if sys.platform == "darwin":
        jvm_flags.append("-Dapple.awt.UIElement=true")
    cmd = ["java", *jvm_flags]
    for k, v in system_props.items():
        cmd.append(f"-D{k}={v}")
    cmd.extend(["-cp", classpath, main_class])
    return cmd


# ---------------------------------------------------------------------------
# Persistent process wrappers for session-scoped JVM reuse
# ---------------------------------------------------------------------------


class BridgeSession:
    """Persistent MCP bridge JVM accessed via JSON-RPC over HTTP.

    Sends JSON-RPC requests to the bridge's MCP HTTP server and receives
    responses with natural HTTP timeouts. Avoids the MCP SDK's subprocess
    management so we can keep the JVM alive across multiple golden tests.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._id = 0

    def _rpc(self, method: str, params: dict | None = None, timeout: int = 120) -> dict:
        self._id += 1
        req: dict = {"jsonrpc": "2.0", "method": method, "id": self._id}
        if params is not None:
            req["params"] = params
        body = json.dumps(req, separators=(",", ":")).encode("utf-8")
        tool_name = (params or {}).get("name", "") if method == "tools/call" else ""
        rpc_label = f"{method}({tool_name})" if tool_name else method
        t0 = time.monotonic()
        print(f"[RPC #{self._id}] -> {rpc_label}", flush=True)
        http_req = urllib.request.Request(
            self._url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(http_req, timeout=timeout) as http_resp:
                resp = json.loads(http_resp.read())
        except urllib.error.URLError as e:
            elapsed = time.monotonic() - t0
            msg = f"Bridge RPC error after {elapsed:.1f}s for {rpc_label}: {e}"
            print(f"[RPC #{self._id}] ERROR: {msg}", flush=True)
            raise RuntimeError(msg) from e
        elapsed = time.monotonic() - t0
        if elapsed > 5:
            print(f"[RPC #{self._id}] <- {rpc_label} OK ({elapsed:.1f}s)", flush=True)
        if "error" in resp and resp["error"] is not None:
            raise RuntimeError(f"MCP error: {resp['error']}")
        return resp["result"]

    def initialize(self) -> dict:
        return self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})

    def list_tool_defs(self) -> list[dict]:
        """Return raw MCP tool definitions."""
        result = self._rpc("tools/list", {})
        return result["tools"]

    def list_tools(self) -> list[str]:
        """Return names of available MCP tools."""
        return [t["name"] for t in self.list_tool_defs()]

    def call_tool(self, name: str, arguments: dict | None = None, timeout: int | None = None) -> str:
        """Call an MCP tool and return the result text (matches execute_tool() return format)."""
        kwargs: dict = {"name": name, "arguments": arguments or {}}
        rpc_kwargs: dict = {}
        if timeout is not None:
            rpc_kwargs["timeout"] = timeout
        result = self._rpc("tools/call", kwargs, **rpc_kwargs)
        return result["content"][0]["text"]

    def close(self) -> None:
        pass

    def is_responsive(self, timeout: int = 5) -> bool:
        """Check if the bridge can respond to RPCs within the given timeout."""
        try:
            self._rpc("tools/list", {}, timeout=timeout)
            return True
        except (RuntimeError, json.JSONDecodeError):
            return False


@dataclasses.dataclass(frozen=True)
class BridgeLogOffsets:
    """Byte offsets into the current live bridge log files for one test."""

    bridge_log_path: Path
    bridge_log_offset: int
    bridge_event_log_path: Path
    bridge_event_log_offset: int


class BridgeManager:
    """Manages a persistent sleepwalker bridge JVM with automatic restart on failure.

    Encapsulates the bridge JVM lifecycle (start, stop, health check, restart).
    Used by session-scoped pytest fixtures to provide fault tolerance: if a test
    leaves the bridge in a stuck state, the next test detects this and restarts
    the JVM rather than cascading failures across all subsequent tests.
    """

    _HEALTH_CHECK_TIMEOUT = 5  # seconds
    _SERVER_CLEANUP_DELAY = 2  # seconds for XMage server to detect disconnection

    def __init__(
        self,
        server: str,
        port: int,
        project_root: Path,
        username: str = "TestPlayer",
        label: str = "bridge",
    ) -> None:
        self._server = server
        self._port = port
        self._project_root = project_root
        self._username = username
        self._label = label
        self.session: BridgeSession | None = None
        self._proc: subprocess.Popen | None = None
        self._log_fh: object | None = None
        self._current_log_path: Path | None = None
        self._current_event_log_path: Path | None = None
        self._needs_reconnect_validation = False

    def _log_dir(self) -> Path:
        return self._project_root / "tmp" / f"golden-{self._label}"

    def _prepare_live_log_path(self, filename: str = "bridge.log") -> Path:
        """Rotate a live log so restarts preserve earlier bridge output."""
        log_dir = self._log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        live_log = log_dir / filename
        suffix = "".join(live_log.suffixes)
        stem = live_log.name[: -len(suffix)] if suffix else live_log.name
        if live_log.exists():
            archive_index = 1
            while True:
                archived_log = log_dir / f"{stem}.{archive_index}{suffix}"
                if not archived_log.exists():
                    live_log.rename(archived_log)
                    break
                archive_index += 1
        if filename == "bridge.log":
            self._current_log_path = live_log
        if filename == "bridge-events.jsonl":
            self._current_event_log_path = live_log
        return live_log

    @staticmethod
    def _sanitize_snapshot_stem(golden_name: str) -> str:
        stem = re.sub(r"[^0-9A-Za-z_.-]+", "_", golden_name).strip("._")
        assert stem, "golden_name must contain at least one filename-safe character"
        return stem

    @staticmethod
    def _log_size(path: Path) -> int:
        if not path.exists():
            return 0
        return path.stat().st_size

    @staticmethod
    def _slice_log_bytes(path: Path, offset: int) -> bytes:
        if not path.exists():
            assert offset == 0, f"Missing log file {path} after recording non-zero offset {offset}"
            return b""
        data = path.read_bytes()
        assert offset <= len(data), f"Offset {offset} exceeds log size {len(data)} for {path}"
        return data[offset:]

    def capture_log_offsets(self) -> BridgeLogOffsets:
        """Capture the current end offsets so one test can snapshot only its own log slice."""
        assert self._current_log_path is not None, "Bridge log path must be set before capturing offsets"
        assert self._current_event_log_path is not None, "Bridge event log path must be set before capturing offsets"
        return BridgeLogOffsets(
            bridge_log_path=self._current_log_path,
            bridge_log_offset=self._log_size(self._current_log_path),
            bridge_event_log_path=self._current_event_log_path,
            bridge_event_log_offset=self._log_size(self._current_event_log_path),
        )

    def write_test_log_snapshots(self, golden_name: str, offsets: BridgeLogOffsets) -> tuple[Path, Path]:
        """Write per-test bridge log slices alongside the live session log files."""
        snapshot_stem = self._sanitize_snapshot_stem(golden_name)
        bridge_snapshot = self._prepare_live_log_path(f"{snapshot_stem}.bridge.log")
        bridge_snapshot.write_bytes(self._slice_log_bytes(offsets.bridge_log_path, offsets.bridge_log_offset))
        event_snapshot = self._prepare_live_log_path(f"{snapshot_stem}.bridge-events.jsonl")
        event_snapshot.write_bytes(
            self._slice_log_bytes(offsets.bridge_event_log_path, offsets.bridge_event_log_offset)
        )
        return bridge_snapshot, event_snapshot

    def assert_clean_reconnect(self, context: str) -> None:
        """Fail fast if a restarted bridge inherited callbacks from old games."""
        if not self._needs_reconnect_validation:
            return
        self._needs_reconnect_validation = False
        assert self._current_log_path is not None, "Bridge log path must be set before reconnect validation"
        log_text = self._current_log_path.read_text(encoding="utf-8", errors="replace")

        started_game_ids: list[str] = []
        for match in re.finditer(r"Game started: gameId=([0-9a-f-]+)", log_text):
            game_id = match.group(1)
            if game_id not in started_game_ids:
                started_game_ids.append(game_id)

        stale_callback_lines = [
            line.strip()
            for line in log_text.splitlines()
            if "Ignoring " in line and ("for non-current game " in line or "for inactive game " in line)
        ]

        if len(started_game_ids) <= 1 and not stale_callback_lines:
            return

        details: list[str] = []
        if len(started_game_ids) > 1:
            details.append("gameIds=" + ", ".join(started_game_ids))
        if stale_callback_lines:
            preview = "; ".join(stale_callback_lines[:3])
            if len(stale_callback_lines) > 3:
                preview += "; ..."
            details.append("staleCallbacks=" + preview)

        raise RuntimeError(
            f"{context}: {self._label.title()} restarted into leaked game state "
            f"({'; '.join(details)}). Inspect {self._current_log_path}"
        )

    def start(self) -> None:
        """Start the bridge JVM and initialize MCP session."""
        tmp_dir = self._log_dir()
        tmp_dir.mkdir(parents=True, exist_ok=True)

        mcp_port_res = find_available_port(19000)
        mcp_port = mcp_port_res.port
        bridge_log = self._prepare_live_log_path()
        bridge_event_log = self._prepare_live_log_path("bridge-events.jsonl")

        with timed_phase("session", f"{self._label}_classpath"):
            bridge_cp = compute_module_classpath(self._project_root, "Mage.Client.Bridge")
        bridge_cmd = _build_java_cmd(
            bridge_cp,
            MAIN_CLASS_BRIDGE,
            {
                "xmage.bridge.server": self._server,
                "xmage.bridge.port": str(self._port),
                "xmage.bridge.personality": "sleepwalker",
                "xmage.bridge.keepAlive": "true",
                "xmage.bridge.mcpPort": str(mcp_port),
                "xmage.bridge.username": self._username,
                "xmage.bridge.bridgelog": str(bridge_event_log),
            },
            max_heap="256m",
        )
        self._log_fh = open(bridge_log, "w")

        self._proc = subprocess.Popen(
            bridge_cmd,
            cwd=self._project_root / "Mage.Client.Bridge",
            stdin=subprocess.PIPE,
            stdout=self._log_fh,
            stderr=subprocess.STDOUT,
            preexec_fn=jvm_oom_preexec_fn(),
        )

        label = self._label.title()
        print(f"{label} JVM started (pid={self._proc.pid}), waiting for MCP on port {mcp_port}...", flush=True)
        assert wait_for_port("127.0.0.1", mcp_port, 120), (
            f"Bridge MCP HTTP server did not start on port {mcp_port} within 120s"
        )
        mcp_port_res.release()

        self.session = BridgeSession(f"http://127.0.0.1:{mcp_port}/mcp")
        self.session.initialize()
        print(f"{self._label.title()} MCP initialized via HTTP", flush=True)
        assert self._proc is not None, "Bridge process must exist after successful start"
        register_observed_process(self._label, self._proc.pid)
        record_registered_rss_snapshot(f"{self._label}_ready", [self._label])

    def stop(self) -> None:
        """Kill the bridge JVM and clean up."""
        unregister_observed_process(self._label)
        if self.session:
            self.session.close()
            self.session = None
        if self._proc:
            if self._proc.stdin:
                try:
                    self._proc.stdin.close()
                except (OSError, ValueError):
                    pass
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                kill_tree(self._proc.pid)
            self._proc = None
        if self._log_fh:
            self._log_fh.close()
            self._log_fh = None

    def is_healthy(self) -> bool:
        """Check if the bridge can respond to RPCs."""
        if self.session is None:
            return False
        return self.session.is_responsive(timeout=self._HEALTH_CHECK_TIMEOUT)

    def ensure_healthy(self) -> None:
        """Verify bridge health. Restart if unhealthy."""
        if self.is_healthy():
            return
        print(f"{self._label.title()} unhealthy, restarting JVM...", flush=True)
        self.restart()

    def restart(self) -> None:
        """Restart the bridge JVM and validate that the next game starts cleanly."""
        try:
            self.stop()
            time.sleep(self._SERVER_CLEANUP_DELAY)
            with timed_phase("session", f"{self._label}_jvm_restart"):
                self.start()
        except (AssertionError, OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"{self._label.title()} restart failed") from exc
        self._needs_reconnect_validation = True


class SpectatorProcess:
    """Persistent observer spectator JVM controlled via stdin JSON protocol.

    In keepAlive mode, the spectator reads JSON commands from stdin. Each command
    creates a game table, waits for players to join, starts the match, and
    auto-watches the game.
    """

    def __init__(self, proc: subprocess.Popen[bytes], log_path: Path, *, health_port: int = 0) -> None:
        self.proc = proc
        self.log_path = log_path
        self.health_port = health_port
        assert proc.stdin is not None, "SpectatorProcess requires stdin=PIPE"
        self._stdin = io.TextIOWrapper(proc.stdin, encoding="utf-8", line_buffering=True)

    def start_game(
        self,
        game_dir: Path,
        players_config: dict,
        choosing_player: str,
    ) -> None:
        """Send a JSON command to create a new game table."""
        cmd = {
            "gameDir": str(game_dir),
            "playersConfig": players_config,
            "choosingPlayer": choosing_player,
            "skipInitShuffling": True,
            "winsNeeded": 1,
        }
        self._stdin.write(json.dumps(cmd, separators=(",", ":")) + "\n")
        self._stdin.flush()

    def wait_for_ready(self, game_dir: Path, timeout: int = SPECTATOR_READY_TIMEOUT_SECONDS) -> str:
        """Wait for the spectator to create the table and be ready for players.

        Uses the HTTP health endpoint for long-poll readiness detection.
        Returns the tableId string for bridge clients to join.
        """
        assert self.health_port > 0, "SpectatorProcess requires health_port for readiness detection"
        return _wait_for_game_ready(self.health_port, game_dir, timeout=timeout)

    def wait_for_watching(self, game_dir: Path, timeout: int = SPECTATOR_READY_TIMEOUT_SECONDS) -> None:
        """Wait for the spectator to attach to the actual game before replay starts."""
        assert self.health_port > 0, "SpectatorProcess requires health_port for watch detection"
        _wait_for_game_watching(self.health_port, game_dir, timeout=timeout)

    def wait_for_game_end(self, game_dir: Path, timeout: int = 30) -> None:
        """Wait for the spectator to signal that event files are fully written."""
        assert self.health_port > 0, "SpectatorProcess requires health_port for game-end detection"
        _wait_for_game_end_http(self.health_port, game_dir, timeout=timeout)

    def close(self) -> None:
        try:
            self._stdin.close()
        except (OSError, ValueError):
            pass


def _run_replay_on_bridge(
    bridge: BridgeSession,
    script: list[dict],
    game_dir: Path,
    player_name: str,
    skip_postscript: bool = False,
    write_log: bool = True,
    should_concede: bool = True,
) -> list[dict]:
    """Execute a replay script on an existing BridgeSession and return the captured prompt.

    Delegates to ``execute_replay_script`` from ``puppeteer.replay`` — the same
    core that the subprocess path uses — so script execution logic lives in one place.

    When ``write_log`` is True, writes ``{player}_llm.jsonl`` so ``build_export``
    can produce a full export.  Player B should set this to False — two concurrent
    writers produce nondeterministic event ordering in the export.

    When ``should_concede`` is False, skip the concede after the script finishes.
    Player B must not concede — it would end the game while player A is still
    executing.  The defensive concede in ``run_golden_scenario`` handles cleanup.
    """
    # Use a config path anchored to the repo root so prompts.json resolves
    # regardless of the pytest working directory.
    config_anchor = REPO_ROOT / "puppeteer" / "prompts.json"
    prompts = load_prompts(config_anchor)
    assert "default" in prompts, "prompts.json must contain a 'default' key"
    system_prompt = prompts["default"]

    game_log = None
    if write_log:
        # Filter out keepAlive-only tools (join_table) so the available_tools
        # list matches what a non-keepAlive bridge would report.
        tool_names = [t for t in bridge.list_tools() if t != "join_table"]
        game_log = GameLogWriter(game_dir, player_name)
        game_log.__enter__()
        game_log.emit("game_start", available_tools=tool_names)

    try:
        # Wrap sync bridge.call_tool as async for execute_replay_script
        async def async_call_tool(name: str, arguments: dict) -> str:
            return bridge.call_tool(name, arguments)

        prompt = asyncio.run(
            execute_replay_script(async_call_tool, script, system_prompt, game_log, skip_postscript=skip_postscript)
        )

        if write_log:
            # Write prompt to file for debugging / golden comparison
            prompt_path = game_dir / f"{player_name}_golden_prompt.json"
            prompt_path.write_text(json.dumps(prompt, indent=2, sort_keys=True, ensure_ascii=False) + "\n")

        if should_concede:
            # Concede to end the game (no-op if game already ended from opponent)
            bridge.call_tool("concede", {})

        if game_log is not None:
            game_log.emit("game_end", reason="replay_script_complete")
    finally:
        if game_log is not None:
            game_log.__exit__(None, None, None)

    return prompt


@dataclasses.dataclass(frozen=True)
class _MCPToolDef:
    name: str
    description: str
    inputSchema: dict | None


def _pilot_script_from_replay_script(script: list[dict]) -> list[dict]:
    """Translate replay-harness scripts into production pilot LLM turns.

    The replay harness scripts start with ``pass_priority`` to obtain the
    opening decision. The real pilot does that internally in
    ``_prefetch_first_action()``, so prompt goldens must drop that first
    scripted step to match production history exactly.
    """
    tool_steps = [step for step in script if not _is_meta_script_step(step)]
    assert tool_steps, "Golden pilot script must contain at least the initial pass_priority"
    first = tool_steps[0]
    assert first.get("name") == "pass_priority", (
        "Golden pilot scripts must start with pass_priority so production prefetch can consume the opening decision."
    )
    assert first.get("arguments", {}) == {}, (
        "Golden pilot scripts must start with pass_priority({}) because the real "
        "pilot prefetch does not send arguments."
    )
    return tool_steps[1:]


def _build_openai_tools_for_pilot(bridge: BridgeSession) -> list[dict]:
    """Build the production OpenAI tool list from the bridge's MCP tool defs."""
    tool_defs = [
        _MCPToolDef(
            name=tool["name"],
            description=tool.get("description", ""),
            inputSchema=tool.get("inputSchema"),
        )
        for tool in bridge.list_tool_defs()
        if tool["name"] != "join_table"
    ]
    return mcp_tools_to_openai(tool_defs)


class _AsyncBridgeSession:
    """Async adapter for the sync HTTP bridge wrapper used by golden tests."""

    def __init__(self, bridge: BridgeSession, execution_state: _ScriptedExecutionState | None = None) -> None:
        self._bridge = bridge
        self._execution_state = execution_state

    async def call_tool(self, name: str, arguments: dict) -> SimpleNamespace:
        text = self._bridge.call_tool(name, arguments)
        if self._execution_state is not None:
            self._execution_state.last_tool_name = name
            self._execution_state.last_result_text = text
        return SimpleNamespace(content=[SimpleNamespace(text=text)])


@dataclasses.dataclass(frozen=True)
class _ScriptedFunctionCall:
    name: str
    arguments: str


@dataclasses.dataclass(frozen=True)
class _ScriptedToolCall:
    id: str
    function: _ScriptedFunctionCall


@dataclasses.dataclass(frozen=True)
class _ScriptedMessage:
    content: str | None
    tool_calls: list[_ScriptedToolCall]


@dataclasses.dataclass(frozen=True)
class _ScriptedChoice:
    finish_reason: str
    message: _ScriptedMessage


class _ScriptedResponse:
    def __init__(self, step: dict, call_index: int) -> None:
        tool_call = _ScriptedToolCall(
            id=f"call_{call_index}",
            function=_ScriptedFunctionCall(
                name=step["name"],
                arguments=json.dumps(step.get("arguments", {})),
            ),
        )
        self.choices = [_ScriptedChoice(finish_reason="tool_calls", message=_ScriptedMessage(None, [tool_call]))]
        self.usage = None

    def model_dump(self) -> dict:
        choice = self.choices[0]
        tool_call = choice.message.tool_calls[0]
        return {
            "choices": [
                {
                    "finish_reason": choice.finish_reason,
                    "message": {
                        "content": choice.message.content,
                        "tool_calls": [
                            {
                                "id": tool_call.id,
                                "function": {
                                    "name": tool_call.function.name,
                                    "arguments": tool_call.function.arguments,
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": None,
        }


@dataclasses.dataclass
class _CapturedPilotRequest:
    last_messages: list[dict] | None = None
    post_script_messages: list[dict] | None = None


@dataclasses.dataclass
class _ScriptedExecutionState:
    last_tool_name: str | None = None
    last_result_text: str | None = None


class _ScriptedChatCompletions:
    def __init__(
        self,
        script: list[dict],
        capture: _CapturedPilotRequest,
        execution_state: _ScriptedExecutionState | None = None,
    ) -> None:
        self._script = script
        self._capture = capture
        self._execution_state = execution_state
        self._step_index = 0
        self._call_index = 0

    async def create(self, **kwargs) -> _ScriptedResponse:
        self._capture.last_messages = json.loads(json.dumps(kwargs["messages"]))
        while self._step_index < len(self._script) and _is_meta_script_step(self._script[self._step_index]):
            _run_meta_script_step(
                self._script[self._step_index],
                last_tool_name=self._execution_state.last_tool_name if self._execution_state else None,
                last_result_text=self._execution_state.last_result_text if self._execution_state else None,
            )
            self._step_index += 1
        if self._step_index >= len(self._script):
            self._capture.post_script_messages = self._capture.last_messages
            raise asyncio.CancelledError()
        self._call_index += 1
        response = _ScriptedResponse(self._script[self._step_index], self._call_index)
        self._step_index += 1
        return response


class _ScriptedOpenAIClient:
    def __init__(
        self,
        script: list[dict],
        capture: _CapturedPilotRequest,
        execution_state: _ScriptedExecutionState | None = None,
    ) -> None:
        self.chat = SimpleNamespace(completions=_ScriptedChatCompletions(script, capture, execution_state))


def _run_pilot_on_bridge(
    bridge: BridgeSession,
    script: list[dict],
    game_dir: Path,
    player_name: str,
    deck_path: str,
    should_concede: bool = True,
) -> list[dict]:
    """Run the real pilot loop with a scripted fake client and capture the prompt."""
    config_anchor = REPO_ROOT / "puppeteer" / "prompts.json"
    prompts = load_prompts(config_anchor)
    assert "default" in prompts, "prompts.json must contain a 'default' key"
    system_prompt = prompts["default"]

    openai_tools = _build_openai_tools_for_pilot(bridge)
    tool_names = [tool["function"]["name"] for tool in openai_tools]
    pilot_script = _pilot_script_from_replay_script(script)
    capture = _CapturedPilotRequest()
    execution_state = _ScriptedExecutionState()
    client = _ScriptedOpenAIClient(pilot_script, capture, execution_state)
    session = _AsyncBridgeSession(bridge, execution_state)

    game_log = GameLogWriter(game_dir, player_name)
    game_log.__enter__()
    game_log.emit(
        "game_start",
        model=DEFAULT_MODEL,
        system_prompt=system_prompt,
        available_tools=tool_names,
        deck_path=str(REPO_ROOT / deck_path),
    )

    try:
        try:
            asyncio.run(
                run_pilot_loop(
                    session=session,
                    client=client,
                    model=DEFAULT_MODEL,
                    system_prompt=system_prompt,
                    tools=openai_tools,
                    prices={},
                    username=player_name,
                    game_dir=game_dir,
                    game_log=game_log,
                )
            )
        except asyncio.CancelledError:
            pass

        prompt = capture.post_script_messages or capture.last_messages
        assert prompt is not None, "Scripted pilot did not capture a prompt"
        prompt_path = game_dir / f"{player_name}_golden_prompt.json"
        prompt_path.write_text(json.dumps(prompt, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        return prompt
    finally:
        if should_concede:
            bridge.call_tool("concede", {})
        game_log.emit("game_end", total_cost_usd=round(game_log.last_cumulative_cost_usd(), 6))
        game_log.__exit__(None, None, None)


def _is_game_over(data: dict) -> bool:
    return bool(data.get("game_over") or data.get("player_dead") or data.get("stop_reason") == "game_over")


def _run_opponent_autopass(bridge: BridgeSession) -> None:
    """Auto-pass for the opponent until the game ends.

    Uses ``pass_priority(until=end_of_turn)`` to batch-handle callbacks
    inside Java without per-callback HTTP round-trips. ``until=end_of_turn``
    now clears when the turn advances, so the harness must explicitly decline
    ``playable_cards`` prompts on the next turn.

    Falls back to ``choose_action`` only for the callbacks that
    ``pass_priority`` cannot handle automatically (combat declarations,
    GAME_CHOOSE_ABILITY, GAME_CHOOSE_CHOICE, or a playable-cards prompt that
    the autopass opponent should decline).
    """
    while True:
        result = bridge.call_tool("pass_priority", {"until": "end_of_turn"})
        data = json.loads(result)
        if _is_game_over(data):
            break
        stop_reason = data.get("stop_reason")
        if stop_reason in ("non_priority_action", "combat", "playable_cards"):
            # pass_priority can't auto-handle these; use choose_action.
            result = bridge.call_tool("choose_action", {"choice": "no"})
            data = json.loads(result)
            if _is_game_over(data):
                break
            # Prompts that don't accept "no" (GAME_CHOOSE_ABILITY,
            # GAME_CHOOSE_CHOICE) need an index — select the first option.
            if not data.get("success") and data.get("retryable"):
                result = bridge.call_tool("choose_action", {"choice": "0"})
                data = json.loads(result)
                if _is_game_over(data):
                    break


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def read_health_port_file(path: Path, timeout: float = 30.0) -> int:
    """Poll for a health port file written by the Java observer and return the port.

    The file is written atomically (rename) by the observer after successfully
    binding the health HTTP server.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            text = path.read_text().strip()
            if text:
                return int(text)
        except FileNotFoundError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"Health port file {path} was not written within {timeout}s")


def _wait_for_health(port: int, timeout: int = 120) -> None:
    """Wait for observer health endpoint to report lobby ready (long-poll)."""
    url = f"http://127.0.0.1:{port}/health?timeout={timeout}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
        data = json.loads(resp.read())
        if data.get("status") != "ready":
            raise RuntimeError(f"Observer health returned unexpected status: {data}")


def _wait_for_commands(port: int, timeout: int = 120) -> None:
    """Wait for observer startup to reach the keepAlive command-loop phase."""
    url = f"http://127.0.0.1:{port}/wait-for-commands?timeout={timeout}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
        data = json.loads(resp.read())
        if data.get("status") != "ready":
            raise RuntimeError(f"Observer wait-for-commands returned unexpected status: {data}")


def _wait_for_game_ready(port: int, game_dir: Path, timeout: int = SPECTATOR_READY_TIMEOUT_SECONDS) -> str:
    """Wait for observer to create a game table via long-poll HTTP endpoint.

    Returns the tableId string once bridge clients can join the table.
    """
    url = f"http://127.0.0.1:{port}/wait-for-ready"
    body = json.dumps({"gameDir": str(game_dir), "timeout": timeout}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
            data = json.loads(resp.read())
            if not data.get("ready"):
                raise RuntimeError(f"Wait-for-ready returned: {data}")
            return data["tableId"]
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        raise RuntimeError(f"Wait-for-ready failed (HTTP {e.code}): {error_body}") from e


def _wait_for_game_watching(port: int, game_dir: Path, timeout: int = SPECTATOR_READY_TIMEOUT_SECONDS) -> None:
    """Wait for observer to attach to the game's actual GameView via HTTP endpoint."""
    url = f"http://127.0.0.1:{port}/wait-for-watching"
    body = json.dumps({"gameDir": str(game_dir), "timeout": timeout}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
            data = json.loads(resp.read())
            if not data.get("watching"):
                raise RuntimeError(f"Wait-for-watching returned: {data}")
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        raise RuntimeError(f"Wait-for-watching failed (HTTP {e.code}): {error_body}") from e


def _wait_for_game_end_http(port: int, game_dir: Path, timeout: int = 30) -> None:
    """Wait for observer to signal game-end via long-poll HTTP endpoint.

    Blocks until the spectator's event files are fully written and closed.
    The server's event file is guaranteed complete by the time the spectator
    signals (the server fires game_end before notifying the spectator).
    """
    url = f"http://127.0.0.1:{port}/wait-for-game-end"
    body = json.dumps({"gameDir": str(game_dir), "timeout": timeout}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
            data = json.loads(resp.read())
            if not data.get("done"):
                raise RuntimeError(f"Wait-for-game-end returned: {data}")
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        raise RuntimeError(f"Wait-for-game-end failed (HTTP {e.code}): {error_body}") from e


def run_golden_scenario(
    server: str,
    port: int,
    project_root: Path,
    game_dir: Path,
    deck_a: str,
    deck_b: str,
    script_a: list[dict],
    golden_name: str,
    bridge_a: BridgeManager,
    bridge_b: BridgeManager,
    spectator: SpectatorProcess,
    script_b: list[dict] | None = None,
    player_a_name: str = "TestPlayer",
    player_b_name: str = "Opponent",
    game_type: str = "Two Player Duel",
    deck_type: str = "Constructed - Legacy",
) -> list[dict]:
    """Run a golden test scenario with a production pilot and replay opponent.

    Player A uses the real production pilot loop with a scripted fake LLM.
    Player B uses ``script_b`` if provided, otherwise auto-passes every
    priority window until the game ends.
    Player A's prompt is captured and compared against golden files.

    Automatically asserts golden prompt and export comparisons using
    ``golden_name`` as the file identifier.

    Returns the captured prompt messages array (what the LLM would see).
    """
    game_dir.mkdir(parents=True, exist_ok=True)

    _write_game_meta(
        game_dir,
        game_type,
        deck_type,
        player_a_name,
        "pilot",
        deck_a,
        player_b_name,
        "replay",
        deck_b,
    )

    # Ensure both bridges are in a clean state from the previous test.
    if not bridge_a.is_healthy():
        bridge_a.ensure_healthy()
        bridge_b.ensure_healthy()
    elif not bridge_b.is_healthy():
        bridge_b.ensure_healthy()

    session_a = bridge_a.session
    session_b = bridge_b.session
    assert session_a is not None, "Bridge A session must be initialized before running a golden scenario"
    assert session_b is not None, "Bridge B session must be initialized before running a golden scenario"
    bridge_a_log_offsets = bridge_a.capture_log_offsets()
    bridge_b_log_offsets = bridge_b.capture_log_offsets()

    replay_errors: list[tuple[str, Exception]] = []

    try:
        with timed_phase(golden_name, "spectator_command"):
            table_id = _send_spectator_command(
                spectator,
                game_dir,
                deck_a,
                deck_b,
                player_a_name,
                player_b_name,
                "replay",
                game_type,
                deck_type,
            )

        # Both bridges join the table concurrently
        join_errors: list[tuple[str, Exception]] = []

        def _join_a() -> None:
            try:
                session_a.call_tool(
                    "join_table",
                    {
                        "deck_path": str(project_root / deck_a),
                        "table_id": table_id,
                    },
                )
            except (RuntimeError, json.JSONDecodeError) as exc:
                join_errors.append(("bridge_a", exc))

        def _join_b() -> None:
            try:
                session_b.call_tool(
                    "join_table",
                    {
                        "deck_path": str(project_root / deck_b),
                        "table_id": table_id,
                    },
                )
            except (RuntimeError, json.JSONDecodeError) as exc:
                join_errors.append(("bridge_b", exc))

        with timed_phase(golden_name, "bridge_join"):
            t_a = threading.Thread(target=_join_a)
            t_b = threading.Thread(target=_join_b)
            t_a.start()
            t_b.start()
            t_a.join(timeout=120)
            t_b.join(timeout=120)
            if join_errors:
                labels = ", ".join(f"{lbl}: {exc}" for lbl, exc in join_errors)
                raise RuntimeError(f"Bridge join failed: {labels}")
        bridge_a.assert_clean_reconnect(f"{golden_name}/bridge_join")
        bridge_b.assert_clean_reconnect(f"{golden_name}/bridge_join")
        with timed_phase(golden_name, "spectator_watch"):
            spectator.wait_for_watching(game_dir)

        # Run player A's scripted production pilot and player B concurrently
        prompt_a: list[dict] | None = None

        def _replay_a() -> None:
            nonlocal prompt_a
            try:
                prompt_a = _run_pilot_on_bridge(
                    session_a,
                    script_a,
                    game_dir,
                    player_a_name,
                    deck_a,
                )
            except (AssertionError, RuntimeError, OSError, json.JSONDecodeError) as exc:
                replay_errors.append(("player_a", exc))

        def _replay_b() -> None:
            try:
                if script_b is not None:
                    _run_replay_on_bridge(
                        session_b,
                        script_b,
                        game_dir,
                        player_b_name,
                        skip_postscript=True,
                        write_log=False,
                        should_concede=False,
                    )
                else:
                    _run_opponent_autopass(session_b)
            except (AssertionError, RuntimeError, OSError, json.JSONDecodeError) as exc:
                replay_errors.append(("player_b", exc))

        with timed_phase(golden_name, "replay"):
            t_a = threading.Thread(target=_replay_a)
            t_b = threading.Thread(target=_replay_b)
            t_a.start()
            t_b.start()
            t_a.join(timeout=180)
            t_b.join(timeout=180)

        record_registered_rss_snapshot(
            f"{golden_name}_post_replay",
            ["server", bridge_a._label, bridge_b._label, "spectator"],
        )

        # Player A errors are fatal; player B errors are usually benign
        # (game ended from player A's concede)
        for lbl, exc in replay_errors:
            if lbl == "player_a":
                raise exc
        for lbl, exc in replay_errors:
            if lbl == "player_b":
                print(f"  [{golden_name}] Player B error (likely benign): {exc}", flush=True)

        assert prompt_a is not None, "Player A script did not produce a prompt"

        with timed_phase(golden_name, "game_end_signal"):
            spectator.wait_for_game_end(game_dir)

        with timed_phase(golden_name, "prompt_compare"):
            assert_golden_prompt(golden_name, prompt_a)

        with timed_phase(golden_name, "export_build"):
            export_data = build_export(game_dir)

        with timed_phase(golden_name, "export_compare"):
            assert_golden_export(golden_name, export_data)

        annotated_blunders = _script_blunder_indices(script_a)
        if annotated_blunders:
            with timed_phase(golden_name, "blunder_extract"):
                decisions = extract_blunder_decisions(export_data, game_dir)
            with timed_phase(golden_name, "blunder_prompt_compare"):
                assert_golden_blunder_prompts(golden_name, export_data, annotated_blunders, decisions)

        return prompt_a

    finally:
        # Defensive concede on both bridges so they're ready for the next test.
        # If a replay worker or cleanup RPC already left a keepAlive bridge in a
        # terminal state, restart it so the cleanup path does not mask the real
        # scenario outcome or poison the next test.
        primary_exc = sys.exc_info()[1]
        cleanup_restarts: list[BridgeManager] = []
        cleanup_restart_failures: list[tuple[str, RuntimeError]] = []
        replay_error_by_label = dict(replay_errors)
        for label, session, bridge in [
            ("player_a", session_a, bridge_a),
            ("player_b", session_b, bridge_b),
        ]:
            replay_exc = replay_error_by_label.get(label)
            if replay_exc is not None:
                print(
                    "  "
                    f"[{golden_name}] Skipping defensive concede for {label}: "
                    f"replay already failed with {replay_exc}",
                    flush=True,
                )
                cleanup_restarts.append(bridge)
                continue
            try:
                session.call_tool("concede", timeout=DEFENSIVE_CONCEDE_TIMEOUT_SECONDS)
            except RuntimeError as exc:
                print(
                    f"  [{golden_name}] Cleanup concede failed for {label}: {exc}. Restarting {bridge._label}.",
                    flush=True,
                )
                cleanup_restarts.append(bridge)

        bridge_a.write_test_log_snapshots(golden_name, bridge_a_log_offsets)
        bridge_b.write_test_log_snapshots(golden_name, bridge_b_log_offsets)

        for bridge in cleanup_restarts:
            try:
                bridge.restart()
            except RuntimeError as exc:
                print(
                    f"  [{golden_name}] Restart failed for {bridge._label}: {exc}",
                    flush=True,
                )
                cleanup_restart_failures.append((bridge._label, exc))

        if cleanup_restart_failures:
            details = "; ".join(f"{label}: {exc}" for label, exc in cleanup_restart_failures)
            if primary_exc is not None:
                primary_exc.add_note(
                    f"Cleanup restart failures while preserving the primary scenario exception: {details}"
                )
            else:
                raise RuntimeError(
                    f"Golden cleanup restart failed after scenario success: {details}"
                ) from cleanup_restart_failures[0][1]


def _write_game_meta(
    game_dir: Path,
    game_type: str,
    deck_type: str,
    player_a_name: str,
    player_a_type: str,
    deck_a: str,
    player_b_name: str,
    player_b_type: str,
    deck_b: str,
) -> None:
    """Write game_meta.json so build_export finds harness_epoch and player info."""
    player_a: dict = {"type": player_a_type, "name": player_a_name, "deck_path": deck_a}
    if player_a_type == "pilot":
        player_a["model"] = "test/golden"
    player_b: dict = {"type": player_b_type, "name": player_b_name, "deck_path": deck_b}
    if player_b_type == "pilot":
        player_b["model"] = "test/golden"
    meta = {
        "harness_epoch": HARNESS_EPOCH,
        "season": 1,
        "game_type": game_type,
        "deck_type": deck_type,
        "players": [player_a, player_b],
    }
    (game_dir / "game_meta.json").write_text(json.dumps(meta, indent=2) + "\n")


def _send_spectator_command(
    spectator: SpectatorProcess,
    game_dir: Path,
    deck_a: str,
    deck_b: str,
    player_a_name: str,
    player_b_name: str,
    player_b_type: str,
    game_type: str,
    deck_type: str,
) -> str:
    """Send a game command to a session-scoped spectator and wait for readiness.

    Returns the tableId string from the spectator.
    """
    players_config = {
        "players": [
            {"type": "replay", "name": player_a_name, "deck": deck_a},
            {"type": player_b_type, "name": player_b_name, "deck": deck_b},
        ],
        "gameType": game_type,
        "deckType": deck_type,
    }
    spectator.start_game(game_dir, players_config, player_a_name)
    return spectator.wait_for_ready(game_dir)


def _to_sorted_json(obj: object) -> str:
    """Deterministic JSON serialization with sorted keys."""
    return json.dumps(_json_ready(obj), indent=2, sort_keys=True, ensure_ascii=False)


def _json_ready(obj: object) -> object:
    """Convert dataclass-backed export records into plain JSON-compatible values."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _json_ready(json_default(obj))
    if isinstance(obj, dict):
        return {key: _json_ready(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_json_ready(value) for value in obj]
    return obj


def _brief(value: object, max_len: int = 80) -> str:
    """Short representation of a JSON value for diff output."""
    value = _json_ready(value)
    if isinstance(value, str):
        r = repr(value)
        if len(r) > max_len:
            return r[: max_len - 3] + "..."
        return r
    s = json.dumps(value, sort_keys=True, ensure_ascii=False, default=json_default)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


def _json_diff(expected: object, actual: object, path: str = "", max_diffs: int = 30) -> list[str]:
    """Structural diff between two parsed JSON values.

    Returns a list of human-readable diff lines with JSON paths, e.g.:
        decisions[0].message: "Play instants" -> "Play instants and abilities"
        actions: 3 items -> 4 items
          [3]: + {"seq": 8, "type": "turn_change"}
    """
    diffs: list[str] = []

    def _recurse(exp: object, act: object, p: str) -> None:
        if len(diffs) >= max_diffs:
            return
        if type(exp) is not type(act):
            diffs.append(f"  {p}: {_brief(exp)} -> {_brief(act)}")
            return
        if isinstance(exp, dict):
            assert isinstance(act, dict)
            exp_keys = set(exp.keys())
            act_keys = set(act.keys())
            for k in sorted(exp_keys - act_keys):
                if len(diffs) >= max_diffs:
                    return
                child = f"{p}.{k}" if p else k
                diffs.append(f"  {child}: - {_brief(exp[k])}")
            for k in sorted(act_keys - exp_keys):
                if len(diffs) >= max_diffs:
                    return
                child = f"{p}.{k}" if p else k
                diffs.append(f"  {child}: + {_brief(act[k])}")
            for k in sorted(exp_keys & act_keys):
                if len(diffs) >= max_diffs:
                    return
                child = f"{p}.{k}" if p else k
                _recurse(exp[k], act[k], child)
        elif isinstance(exp, list):
            assert isinstance(act, list)
            if len(exp) != len(act):
                diffs.append(f"  {p}: {len(exp)} items -> {len(act)} items")
            min_len = min(len(exp), len(act))
            for i in range(min_len):
                if len(diffs) >= max_diffs:
                    return
                _recurse(exp[i], act[i], f"{p}[{i}]")
            for i in range(min_len, len(exp)):
                if len(diffs) >= max_diffs:
                    return
                diffs.append(f"  {p}[{i}]: - {_brief(exp[i])}")
            for i in range(min_len, len(act)):
                if len(diffs) >= max_diffs:
                    return
                diffs.append(f"  {p}[{i}]: + {_brief(act[i])}")
        elif exp != act:
            diffs.append(f"  {p}: {_brief(exp)} -> {_brief(act)}")

    _recurse(expected, actual, path)
    if len(diffs) >= max_diffs:
        diffs.append(f"  ... (truncated, {max_diffs}+ differences)")
    return diffs


def _normalize_prompt_for_golden(obj: object) -> object:
    """Normalize prompt payloads for deterministic golden comparisons.

    - Parse embedded JSON strings and re-serialize with sorted keys.
    """
    obj = _json_ready(obj)
    if isinstance(obj, dict):
        return {key: _normalize_prompt_for_golden(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_normalize_prompt_for_golden(item) for item in obj]
    if isinstance(obj, str):
        try:
            parsed = json.loads(obj)
        except json.JSONDecodeError:
            return obj
        return _normalize_prompt_for_golden(parsed)
    return obj


def assert_golden_prompt(name: str, actual: list[dict]) -> None:
    """Compare prompt messages against golden file, or update in UPDATE_GOLDEN mode."""
    actual_json = _to_sorted_json(_normalize_prompt_for_golden(actual))
    golden_file = GOLDEN_DIR / f"{name}.json"

    if UPDATE_MODE:
        golden_file.parent.mkdir(parents=True, exist_ok=True)
        golden_file.write_text(actual_json + "\n")
        print(f"Updated golden file: {golden_file}")
        return

    assert golden_file.exists(), f"Golden file not found: {golden_file}\nRun 'make regen-golden' to generate it."

    expected = golden_file.read_text().rstrip()
    if expected != actual_json:
        expected_obj = json.loads(expected)
        actual_obj = json.loads(actual_json)
        diff_lines = _json_diff(expected_obj, actual_obj)
        diff_text = "\n".join(diff_lines)
        raise AssertionError(
            f"Golden file mismatch: {name}.json\nRun 'make regen-golden' to regenerate.\n\n{diff_text}"
        )


def _normalize_embedded_json(obj: object) -> object:
    """Normalize embedded JSON strings for deterministic key order.

    MCP tool results are serialized as JSON strings within the export data.
    The key order in these strings can vary between runs (e.g. {"blocks":"p10","id":"p7"}
    vs {"id":"p7","blocks":"p10"}). Parse and re-serialize with sorted keys.
    """
    obj = _json_ready(obj)
    if isinstance(obj, dict):
        return {k: _normalize_embedded_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_embedded_json(item) for item in obj]
    if isinstance(obj, str):
        if not obj.startswith(("{", "[")):
            return obj
        try:
            parsed = json.loads(obj)
            return _normalize_embedded_json(parsed)
        except (json.JSONDecodeError, ValueError):
            return obj
    return obj


def _strip_volatile(data: dict) -> None:
    """Remove only genuinely non-semantic run-to-run noise from export data."""
    # Top-level volatile fields
    data.pop("timestamp", None)

    # Keep critical errors visible in goldens; only strip their wall-clock time.
    for error in data.get("errors", []):
        if isinstance(error, dict):
            error.pop("ts", None)
        else:
            error.ts = None

    # Strip volatile fields from player summaries — convert dataclass instances
    # to plain dicts so downstream json.dumps works.
    players = data.get("players", [])
    for i, player in enumerate(players):
        if dataclasses.is_dataclass(player) and not isinstance(player, type):
            d = {k: v for k, v in dataclasses.asdict(player).items() if v is not None}
            d.pop("thinkingTimeSecs", None)
            players[i] = d
        elif isinstance(player, dict):
            player.pop("thinkingTimeSecs", None)

    # Strip ts from actions
    for action in data.get("actions", []):
        if isinstance(action, dict):
            action.pop("ts", None)
        else:
            action.ts = None

    # Convert llmEvents to dicts (they are dataclass instances after validation)
    # then sort by (seq, player) and strip wall-clock timing fields.
    # Mulligans and concedes have both players acting at the same seq;
    # thread interleaving is nondeterministic so we need a stable sort.
    llm_events = data.get("llmEvents", [])
    for i, event in enumerate(llm_events):
        if dataclasses.is_dataclass(event) and not isinstance(event, type):
            source_keys: frozenset[str] | None = getattr(event, "_source_keys", None)
            if source_keys is not None:
                d = {f.name: getattr(event, f.name) for f in dataclasses.fields(event) if f.name in source_keys}
                extra: dict[str, object] | None = getattr(event, "_extra", None)
                if extra:
                    d.update(extra)
                llm_events[i] = d
            else:
                llm_events[i] = {
                    f.name: getattr(event, f.name)
                    for f in dataclasses.fields(event)
                    if getattr(event, f.name) is not None
                }
    for event in llm_events:
        event.pop("ts", None)
        event.pop("latencyMs", None)
    llm_events.sort(key=lambda e: (e.get("seq", 0), e.get("player", "")))

    # Same for llmTrace.
    for event in data.get("llmTrace", []):
        event.pop("ts", None)
    data.get("llmTrace", []).sort(key=lambda e: (e.get("seq", 0), e.get("player", "")))


def _normalize_export_for_golden(export_data: dict) -> dict:
    """Return a deterministic export copy for golden comparison."""
    normalized = _json_ready(export_data)
    assert isinstance(normalized, dict), f"expected export normalization to produce an object, got {normalized!r}"
    _strip_volatile(normalized)
    normalized = _normalize_embedded_json(normalized)
    # Round-trip through JSON to convert dataclass instances to plain dicts
    return json.loads(json.dumps(normalized, default=json_default))


def assert_golden_export(name: str, export_data: dict) -> None:
    """Compare export data against a golden file."""
    normalized_export = _normalize_export_for_golden(export_data)
    actual_json = _to_sorted_json(normalized_export)
    golden_file = GOLDEN_EXPORTS_DIR / f"{name}.json"

    if UPDATE_MODE:
        golden_file.parent.mkdir(parents=True, exist_ok=True)
        golden_file.write_text(actual_json + "\n")
        print(f"Updated golden export: {golden_file}")
        return

    assert golden_file.exists(), f"Golden export file not found: {golden_file}\nRun 'make regen-golden' to generate it."

    expected = golden_file.read_text().rstrip()
    if expected != actual_json:
        expected_obj = json.loads(expected)
        diff_lines = _json_diff(expected_obj, normalized_export)
        diff_text = "\n".join(diff_lines)
        raise AssertionError(
            f"Golden export mismatch: {name}.json\nRun 'make regen-golden' to regenerate.\n\n{diff_text}"
        )


def _script_blunder_indices(script: list[dict]) -> list[int]:
    """Walk script steps, return decision indices where ``golden_blunder`` is set.

    Decisions are anchored on ``pass_priority`` / ``get_action_choices`` calls
    (decision sources), not on ``choose_action``.  The first ``choose_action``
    after a decision source resolves that decision; subsequent chained
    ``choose_action`` calls (e.g. targeting after a cast) are part of the same
    decision and do NOT increment the index.

    Place ``golden_blunder`` on the *first* ``choose_action`` after a decision
    source — that's the one whose decision index is captured.
    """
    indices: list[int] = []
    decision_idx = 0
    after_decision_source = False

    for step in script:
        name = step.get("name")
        if name in ("pass_priority", "get_action_choices"):
            after_decision_source = True
        elif name == "choose_action":
            if after_decision_source:
                # First choose_action after a decision source = new decision
                if step.get("golden_blunder"):
                    indices.append(decision_idx)
                decision_idx += 1
                after_decision_source = False
            else:
                # Chained choose_action (targeting, second cast, etc.)
                # — still part of previous decision, don't increment.
                assert not step.get("golden_blunder"), (
                    "golden_blunder on chained choose_action (step has no preceding "
                    "pass_priority/get_action_choices). Annotate the first choose_action "
                    "of the decision instead."
                )
    return indices


def extract_blunder_decisions(export_data: dict, game_dir: Path) -> list[dict]:
    """Extract decisions for golden blunder prompt comparisons."""
    # Keep the temp export on the validator's canonical game-export path.
    tmp_export = game_dir / "game_blunder_export.json"
    # Golden helpers now receive dataclass-backed exports from the typed loader,
    # so normalize them back to plain JSON before handing off to file-based tools.
    tmp_export.write_text(json.dumps(_json_ready(export_data)))
    try:
        return extract_decisions(str(tmp_export))
    finally:
        tmp_export.unlink()


def assert_golden_blunder_prompts(
    name: str,
    export_data: dict,
    annotated: list[int],
    decisions: list[Decision],
) -> None:
    """Check blunder analysis prompts for annotated decision indices.

    For each annotated ``choose_action`` in the script, builds the blunder
    evaluation prompt (system + user) from the game export and compares against
    golden reference files.  Skips entirely if no script steps are annotated.
    """
    if not annotated:
        return

    # Build prompt context
    overview = _game_overview(export_data)
    snapshots = export_data.snapshots
    actions = export_data.actions
    abt = _actions_by_turn(actions)
    num_players = len(export_data.players)

    # Oracle cache: load from golden dir, or generate in update mode
    golden_dir = GOLDEN_BLUNDER_DIR / name
    oracle_cache_path = golden_dir / "oracle_cache.json"

    if UPDATE_MODE:
        all_names = _collect_card_names(export_data)
        oracle_texts = _get_oracle_texts(sorted(all_names))
        golden_dir.mkdir(parents=True, exist_ok=True)
        oracle_cache_path.write_text(json.dumps(oracle_texts, indent=2, sort_keys=True) + "\n")
    else:
        assert oracle_cache_path.exists(), (
            f"Oracle cache missing: {oracle_cache_path}\nRun 'make regen-golden' to generate."
        )
        oracle_texts = json.loads(oracle_cache_path.read_text())

    by_index = {decision_index(d): d for d in decisions}

    for idx in annotated:
        assert idx in by_index, (
            f"Decision index {idx} not found in extracted decisions for {name}. "
            f"Available indices: {sorted(by_index.keys())}"
        )
        decision = by_index[idx]
        system, user = build_decision_prompt(
            overview=overview,
            decision=decision,
            oracle_texts=oracle_texts,
            snapshots=snapshots,
            actions_by_turn=abt,
            num_players=num_players,
            all_actions=actions,
        )

        actual = {
            "decision_index": idx,
            "turn": decision.get("turn"),
            "phase": decision.get("phase"),
            "player": decision["player"],
            "message": decision.get("message", ""),
            "system": system,
            "user": user,
        }

        golden_file = golden_dir / f"decision_{idx}.json"
        actual_json = json.dumps(actual, indent=2) + "\n"

        if UPDATE_MODE:
            golden_file.write_text(actual_json)
            print(f"Updated golden blunder prompt: {golden_file}")
            continue

        assert golden_file.exists(), (
            f"Golden blunder prompt missing: {golden_file}\nRun 'make regen-golden' to generate."
        )
        expected = json.loads(golden_file.read_text())

        if actual["system"] != expected["system"]:
            raise AssertionError(
                f"Blunder system prompt changed for {name} decision {idx}\nRun 'make regen-golden' to regenerate."
            )
        if actual["user"] != expected["user"]:
            raise AssertionError(
                f"Blunder user message changed for {name} decision {idx}\nRun 'make regen-golden' to regenerate."
            )
