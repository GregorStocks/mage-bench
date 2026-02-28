"""Shared helpers for golden prompt integration tests.

Runs real XMage games with scripted replay pilots, captures the exact
messages array that would be sent to the LLM, and compares against golden files.

These are integration tests that require compilation and a running XMage server.
They are NOT included in ``make test`` — run them with ``make test-golden``.

To run:    make test-golden
To update: make update-golden
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from puppeteer.harness_epoch import HARNESS_EPOCH
from puppeteer.process_manager import kill_tree

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


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "golden" / "prompts"
GOLDEN_EXPORTS_DIR = Path(__file__).resolve().parent / "golden" / "exports"
GOLDEN_BLUNDER_DIR = Path(__file__).resolve().parent / "golden" / "blunder_prompts"

UPDATE_MODE = os.environ.get("UPDATE_GOLDEN", "").lower() in ("1", "true", "yes")
SPECTATOR_READY_TIMEOUT_SECONDS = 240

# Default decks for tests (relative to project root)
DECK_RED_STOMPY = "Mage.Client/release/sample-decks/Legacy/Red-Stompy.dck"
DECK_GOBLINS = "Mage.Client/release/sample-decks/Legacy/Goblins.dck"

# Custom test decks (relative to project root)
DECK_BOLT_AND_BURN = "puppeteer/tests/decks/bolt_and_burn.dck"
DECK_CLONE_AND_MEMNITE = "puppeteer/tests/decks/clone_and_memnite.dck"
DECK_DARK_DEPTHS_COMBO = "puppeteer/tests/decks/dark_depths_combo.dck"
DECK_FILLER = "puppeteer/tests/decks/filler_opponent.dck"
DECK_MANA_DRAIN_FOF = "puppeteer/tests/decks/mana_drain_fact_or_fiction.dck"
DECK_PLAINS_LIONS = "puppeteer/tests/decks/plains_lions_opponent.dck"
DECK_SAVANNAH_LIONS = "puppeteer/tests/decks/savannah_lions.dck"
DECK_ANCIENT_STIRRINGS = "puppeteer/tests/decks/ancient_stirrings.dck"
DECK_MDFC_LAND_AND_SUSPEND = "puppeteer/tests/decks/mdfc_land_and_suspend.dck"

# Main classes for direct java -cp launches (from each module's pom.xml exec-maven-plugin config)
MAIN_CLASS_OBSERVER = "mage.client.observer.ObserverMain"
MAIN_CLASS_BRIDGE = "mage.client.bridge.BridgeClient"
MAIN_CLASS_SERVER = "mage.server.Main"

# ---------------------------------------------------------------------------
# Classpath computation (cached per module within a pytest session)
# ---------------------------------------------------------------------------

_classpath_cache: dict[str, str] = {}


def compute_module_classpath(project_root: Path, module: str) -> str:
    """Compute the Java classpath for a Maven module, cached per session.

    Runs ``mvn dependency:build-classpath`` on first call per module, then
    returns the cached result on subsequent calls. The classpath includes
    the module's own ``target/classes`` directory prepended to the dependency
    classpath.
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
    )
    assert result.returncode == 0, f"Failed to compute classpath for {module}: {result.stderr}"
    dep_classpath = cp_file.read_text().strip()
    classpath = f"{module_dir / 'target' / 'classes'}:{dep_classpath}"
    _classpath_cache[module] = classpath
    return classpath


def _build_java_cmd(
    classpath: str,
    main_class: str,
    system_props: dict[str, str],
) -> list[str]:
    """Build a ``java -cp`` command with JVM flags and system properties."""
    jvm_flags = ["--add-opens=java.base/java.io=ALL-UNNAMED"]
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

    def _rpc(self, method: str, params: dict | None = None, timeout: int = 60) -> dict:
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

    def list_tools(self) -> list[str]:
        """Return names of available MCP tools."""
        result = self._rpc("tools/list", {})
        return [t["name"] for t in result["tools"]]

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


class PotatoProcess:
    """Persistent potato JVM controlled via stdin line protocol.

    In keepAlive mode, the potato reads deck paths from stdin. Each line triggers
    it to load the deck, reset state, and join the next available game table.
    """

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self.proc = proc
        assert proc.stdin is not None, "PotatoProcess requires stdin=PIPE"
        self._stdin = io.TextIOWrapper(proc.stdin, encoding="utf-8", line_buffering=True)

    def join_next_game(self, deck_path: str) -> None:
        """Send a deck path to trigger the potato to join the next game."""
        self._stdin.write(deck_path + "\n")
        self._stdin.flush()

    def close(self) -> None:
        try:
            self._stdin.close()
        except Exception:
            pass


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

    def wait_for_ready(self, game_dir: Path, timeout: int = SPECTATOR_READY_TIMEOUT_SECONDS) -> None:
        """Wait for the spectator to create the table and be ready for players.

        Uses the HTTP health endpoint for long-poll readiness detection when
        available, falling back to log marker polling otherwise.
        """
        assert self.health_port > 0, "SpectatorProcess requires health_port for readiness detection"
        _wait_for_game_ready(self.health_port, game_dir, timeout=timeout)

    def close(self) -> None:
        try:
            self._stdin.close()
        except Exception:
            pass


def _run_replay_on_bridge(
    bridge: BridgeSession,
    script: list[dict],
    game_dir: Path,
    player_name: str,
) -> list[dict]:
    """Execute a replay script on an existing BridgeSession and return the captured prompt.

    Delegates to ``execute_replay_script`` from ``puppeteer.replay`` — the same
    core that the subprocess path uses — so script execution logic lives in one place.

    Writes ``{player}_llm.jsonl`` so ``build_export`` can produce a full export.
    """
    from puppeteer.config import load_prompts
    from puppeteer.game_log import GameLogWriter
    from puppeteer.replay import execute_replay_script

    # Use a config path anchored to the repo root so prompts.json resolves
    # regardless of the pytest working directory.
    config_anchor = REPO_ROOT / "puppeteer" / "prompts.json"
    prompts = load_prompts(config_anchor)
    assert "default" in prompts, "prompts.json must contain a 'default' key"
    system_prompt = prompts["default"]

    # Filter out keepAlive-only tools (join_table) so the available_tools
    # list matches what a non-keepAlive bridge would report.
    tool_names = [t for t in bridge.list_tools() if t != "join_table"]

    with GameLogWriter(game_dir, player_name) as game_log:
        game_log.emit("game_start", available_tools=tool_names)

        # Wrap sync bridge.call_tool as async for execute_replay_script
        async def async_call_tool(name: str, arguments: dict) -> str:
            return bridge.call_tool(name, arguments)

        prompt = asyncio.run(execute_replay_script(async_call_tool, script, system_prompt, game_log))

        # Write prompt to file for debugging / golden comparison
        prompt_path = game_dir / f"{player_name}_golden_prompt.json"
        prompt_path.write_text(json.dumps(prompt, indent=2, sort_keys=True, ensure_ascii=False) + "\n")

        # Concede to end the game (no-op if game already ended from opponent)
        bridge.call_tool("concede", {})

        game_log.emit("game_end", reason="replay_script_complete")

    return prompt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _start_process(
    args: list[str],
    cwd: Path,
    env_updates: dict[str, str],
    log_path: Path,
) -> tuple[subprocess.Popen, object]:
    """Start a subprocess with logging. Returns (proc, log_file_handle)."""
    env = os.environ.copy()
    env.update(env_updates)
    log_fh = open(log_path, "w")
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    return proc, log_fh


def _wait_for_log_marker(
    log_path: Path,
    marker: str,
    proc: subprocess.Popen,
    timeout: int = 120,
) -> None:
    """Wait for a marker string to appear in a log file."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log_text = log_path.read_text() if log_path.exists() else "<no log>"
            raise RuntimeError(
                f"Process exited (rc={proc.returncode}) before marker appeared.\n"
                f"Marker: {marker!r}\n"
                f"Log tail:\n{log_text[-2000:]}"
            )
        if log_path.exists() and marker in log_path.read_text():
            return
        time.sleep(2)
    log_text = log_path.read_text() if log_path.exists() else "<no log>"
    raise TimeoutError(f"Marker not found within {timeout}s: {marker!r}\nLog tail:\n{log_text[-2000:]}")


def _wait_for_health(port: int, timeout: int = 120) -> None:
    """Wait for observer health endpoint to report lobby ready (long-poll)."""
    url = f"http://127.0.0.1:{port}/health?timeout={timeout}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
        data = json.loads(resp.read())
        if data.get("status") != "ready":
            raise RuntimeError(f"Observer health returned unexpected status: {data}")


def _wait_for_game_ready(port: int, game_dir: Path, timeout: int = SPECTATOR_READY_TIMEOUT_SECONDS) -> None:
    """Wait for observer to create a game table via long-poll HTTP endpoint."""
    url = f"http://127.0.0.1:{port}/wait-for-ready"
    body = json.dumps({"gameDir": str(game_dir), "timeout": timeout}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
            data = json.loads(resp.read())
            if not data.get("ready"):
                raise RuntimeError(f"Wait-for-ready returned: {data}")
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        raise RuntimeError(f"Wait-for-ready failed (HTTP {e.code}): {error_body}") from e


def _wait_for_files_quiescent(paths: list[Path], timeout: int = 30, stable_for: float = 2.0) -> None:
    """Wait until at least one export file exists and observed sizes stop changing."""
    deadline = time.monotonic() + timeout
    last_sizes: tuple[tuple[str, int], ...] | None = None
    stable_since: float | None = None
    while time.monotonic() < deadline:
        snapshot: list[tuple[str, int]] = []
        for path in paths:
            if path.exists():
                snapshot.append((str(path), path.stat().st_size))
        size_tuple = tuple(snapshot)
        if size_tuple and size_tuple == last_sizes:
            if stable_since is None:
                stable_since = time.monotonic()
            elif (time.monotonic() - stable_since) >= stable_for:
                return
        else:
            stable_since = None
            if size_tuple:
                last_sizes = size_tuple
        time.sleep(0.25)
    current = {str(path): (path.stat().st_size if path.exists() else "<missing>") for path in paths}
    raise TimeoutError(f"Game export files did not quiesce within {timeout}s: {current}")


def _wait_for_game_end_event(game_dir: Path, timeout: int = 30) -> None:
    """Wait until server_game_events.jsonl contains a game_end event.

    The spectator writes game_end asynchronously after the game ends on the
    server.  Without this wait, the export pipeline may run before the event
    is written, producing gameOver=null.
    """
    server_events = game_dir / "server_game_events.jsonl"
    spectator_events = game_dir / "game_events.jsonl"
    deadline = time.monotonic() + timeout
    t0 = time.monotonic()
    while time.monotonic() < deadline:
        # Check server events (version 2 export path)
        if server_events.exists():
            text = server_events.read_text()
            if '"game_end"' in text:
                return
        # Fallback: check spectator events (version 1 export path)
        if spectator_events.exists():
            text = spectator_events.read_text()
            if '"game_over"' in text:
                return
        time.sleep(0.25)
    # Dump diagnostic info on timeout
    elapsed = time.monotonic() - t0
    diag_parts = [f"No game_end event found within {elapsed:.1f}s in {game_dir}"]
    for path in [server_events, spectator_events]:
        if path.exists():
            text = path.read_text()
            lines = text.strip().split("\n")
            diag_parts.append(f"  {path.name}: {len(lines)} lines, last: {lines[-1][:200] if lines else '<empty>'}")
        else:
            diag_parts.append(f"  {path.name}: does not exist")
    diag = "\n".join(diag_parts)
    print(diag, flush=True)
    raise TimeoutError(diag)


def run_golden_scenario(
    server: str,
    port: int,
    project_root: Path,
    game_dir: Path,
    deck_a: str,
    deck_b: str,
    script: list[dict],
    golden_name: str,
    player_a_name: str = "TestPlayer",
    player_b_name: str = "Opponent",
    game_type: str = "Two Player Duel",
    deck_type: str = "Constructed - Legacy",
    bridge: BridgeSession | None = None,
    potato: PotatoProcess | None = None,
    spectator: SpectatorProcess | None = None,
) -> list[dict]:
    """Run a golden test scenario with a replay player vs a potato opponent.

    Starts a observer spectator (creates the game table), a replay client
    (executes scripted MCP tool calls and captures the LLM prompt), and a
    potato client (auto-responds to everything as the opponent).

    When ``bridge`` and/or ``potato`` are provided, reuses those session-scoped
    JVM processes instead of spawning fresh ones per test.

    When ``spectator`` is provided, reuses the session-scoped spectator JVM
    instead of spawning a fresh one per test.

    Automatically asserts golden prompt and export comparisons using
    ``golden_name`` as the file identifier.

    Returns the captured prompt messages array (what the LLM would see).
    """
    use_persistent = bridge is not None and potato is not None
    if use_persistent:
        return _run_golden_persistent(
            server,
            port,
            project_root,
            game_dir,
            deck_a,
            deck_b,
            script,
            golden_name,
            player_a_name,
            player_b_name,
            game_type,
            deck_type,
            bridge,
            potato,
            spectator,
        )
    return _run_golden_subprocess(
        server,
        port,
        project_root,
        game_dir,
        deck_a,
        deck_b,
        script,
        golden_name,
        player_a_name,
        player_b_name,
        game_type,
        deck_type,
        spectator,
    )


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
    meta = {
        "harness_epoch": HARNESS_EPOCH,
        "game_type": game_type,
        "deck_type": deck_type,
        "players": [
            {"type": player_a_type, "name": player_a_name, "deck_path": deck_a},
            {"type": player_b_type, "name": player_b_name, "deck_path": deck_b},
        ],
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
) -> None:
    """Send a game command to a session-scoped spectator and wait for readiness."""
    players_config = {
        "players": [
            {"type": "replay", "name": player_a_name, "deck": deck_a},
            {"type": player_b_type, "name": player_b_name, "deck": deck_b},
        ],
        "gameType": game_type,
        "deckType": deck_type,
    }
    spectator.start_game(game_dir, players_config, player_a_name)
    spectator.wait_for_ready(game_dir)


def _start_spectator(
    server: str,
    port: int,
    project_root: Path,
    game_dir: Path,
    deck_a: str,
    deck_b: str,
    player_a_name: str,
    player_b_name: str,
    player_b_type: str,
    game_type: str,
    deck_type: str,
) -> tuple[subprocess.Popen, object, Path]:
    """Start a observer spectator and wait for table creation.

    Returns (proc, log_file_handle, log_path).
    """
    players_config = json.dumps(
        {
            "players": [
                {"type": "replay", "name": player_a_name, "deck": deck_a},
                {"type": player_b_type, "name": player_b_name, "deck": deck_b},
            ],
            "gameType": game_type,
            "deckType": deck_type,
        },
        separators=(",", ":"),
    )

    spectator_log = game_dir / "spectator.log"
    cp = compute_module_classpath(project_root, "Mage.Client.Observer")
    cmd = _build_java_cmd(
        cp,
        MAIN_CLASS_OBSERVER,
        {
            "xmage.aiPuppeteer.autoConnect": "true",
            "xmage.aiPuppeteer.autoStart": "true",
            "xmage.aiPuppeteer.disableWhatsNew": "true",
            "xmage.observer.noWindow": "true",
            "xmage.aiPuppeteer.server": server,
            "xmage.aiPuppeteer.port": str(port),
            "xmage.aiPuppeteer.user": "spectator",
            "xmage.aiPuppeteer.password": "",
            "xmage.observer.gameDir": str(game_dir),
        },
    )

    spectator_proc, spectator_fh = _start_process(
        args=cmd,
        cwd=project_root / "Mage.Client.Observer",
        env_updates={
            "XMAGE_AI_PUPPETEER": "1",
            "XMAGE_AI_PUPPETEER_USER": "spectator",
            "XMAGE_AI_PUPPETEER_PASSWORD": "",
            "XMAGE_AI_PUPPETEER_SERVER": server,
            "XMAGE_AI_PUPPETEER_PORT": str(port),
            "XMAGE_AI_PUPPETEER_DISABLE_WHATS_NEW": "1",
            "XMAGE_AI_PUPPETEER_PLAYERS_CONFIG": players_config,
            "XMAGE_AI_PUPPETEER_SKIP_INIT_SHUFFLING": "true",
            "XMAGE_AI_PUPPETEER_WINS_NEEDED": "1",
            "XMAGE_AI_PUPPETEER_CHOOSING_PLAYER": player_a_name,
        },
        log_path=spectator_log,
    )

    _wait_for_log_marker(
        spectator_log,
        "AI Puppeteer: waiting for",
        spectator_proc,
        timeout=SPECTATOR_READY_TIMEOUT_SECONDS,
    )

    return spectator_proc, spectator_fh, spectator_log


def _run_golden_persistent(
    server: str,
    port: int,
    project_root: Path,
    game_dir: Path,
    deck_a: str,
    deck_b: str,
    script: list[dict],
    golden_name: str,
    player_a_name: str,
    player_b_name: str,
    game_type: str,
    deck_type: str,
    bridge: BridgeSession,
    potato: PotatoProcess,
    spectator: SpectatorProcess | None = None,
) -> list[dict]:
    """Run a golden scenario using session-scoped bridge and potato JVMs."""
    game_dir.mkdir(parents=True, exist_ok=True)
    _write_game_meta(
        game_dir,
        game_type,
        deck_type,
        player_a_name,
        "replay",
        deck_a,
        player_b_name,
        "potato",
        deck_b,
    )

    procs: list[subprocess.Popen] = []
    log_fhs: list = []

    try:
        # Start spectator — reuse session-scoped process if available
        if spectator is not None:
            with timed_phase(golden_name, "spectator_command"):
                _send_spectator_command(
                    spectator,
                    game_dir,
                    deck_a,
                    deck_b,
                    player_a_name,
                    player_b_name,
                    "potato",
                    game_type,
                    deck_type,
                )
        else:
            with timed_phase(golden_name, "spectator_startup"):
                spectator_proc, spectator_fh, _spectator_log = _start_spectator(
                    server,
                    port,
                    project_root,
                    game_dir,
                    deck_a,
                    deck_b,
                    player_a_name,
                    player_b_name,
                    "potato",
                    game_type,
                    deck_type,
                )
                procs.append(spectator_proc)
                log_fhs.append(spectator_fh)

        # Tell potato to join with the new deck (non-blocking: potato starts polling)
        potato.join_next_game(str(project_root / deck_b))

        # Tell bridge to join with the new deck (blocking: waits for game start)
        with timed_phase(golden_name, "bridge_join"):
            bridge.call_tool("join_table", {"deck_path": str(project_root / deck_a)})

        # Execute replay script on the persistent bridge
        with timed_phase(golden_name, "replay"):
            prompt = _run_replay_on_bridge(bridge, script, game_dir, player_a_name)

        # Wait for the spectator to record the game_end event before checking
        # file quiescence — otherwise the files may appear "stable" before
        # the game_end event is written, producing gameOver=null in the export.
        with timed_phase(golden_name, "game_end_wait"):
            _wait_for_game_end_event(game_dir)

        with timed_phase(golden_name, "file_quiescence"):
            _wait_for_files_quiescent([game_dir / "game_events.jsonl", game_dir / "server_game_events.jsonl"])

        with timed_phase(golden_name, "golden_comparison"):
            assert_golden_prompt(golden_name, prompt)
            assert_golden_export(golden_name, game_dir)
            assert_golden_blunder_prompts(golden_name, game_dir, script)

        return prompt

    finally:
        # Concede the bridge game so it's ready for the next test.
        # Without this, a failed test leaves the bridge stuck mid-game
        # and all subsequent persistent tests fail on join_table.
        try:
            bridge.call_tool("concede", timeout=10)
        except Exception:
            pass
        # Kill only the per-test processes — session-scoped ones are managed by fixtures
        for proc in procs:
            if proc.poll() is None:
                kill_tree(proc.pid)
        for fh in log_fhs:
            fh.close()


def _run_golden_subprocess(
    server: str,
    port: int,
    project_root: Path,
    game_dir: Path,
    deck_a: str,
    deck_b: str,
    script: list[dict],
    golden_name: str,
    player_a_name: str,
    player_b_name: str,
    game_type: str,
    deck_type: str,
    spectator: SpectatorProcess | None = None,
) -> list[dict]:
    """Run a golden scenario by spawning fresh subprocess per test (original approach)."""
    game_dir.mkdir(parents=True, exist_ok=True)
    _write_game_meta(
        game_dir,
        game_type,
        deck_type,
        player_a_name,
        "replay",
        deck_a,
        player_b_name,
        "potato",
        deck_b,
    )

    # Write script file
    script_path = game_dir / "script.json"
    script_path.write_text(json.dumps(script))

    procs: list[subprocess.Popen] = []
    log_fhs: list = []

    try:
        # Start spectator — reuse session-scoped process if available
        if spectator is not None:
            with timed_phase(golden_name, "spectator_command"):
                _send_spectator_command(
                    spectator,
                    game_dir,
                    deck_a,
                    deck_b,
                    player_a_name,
                    player_b_name,
                    "potato",
                    game_type,
                    deck_type,
                )
        else:
            with timed_phase(golden_name, "spectator_startup"):
                spectator_proc, spectator_fh, spectator_log = _start_spectator(
                    server,
                    port,
                    project_root,
                    game_dir,
                    deck_a,
                    deck_b,
                    player_a_name,
                    player_b_name,
                    "potato",
                    game_type,
                    deck_type,
                )
                procs.append(spectator_proc)
                log_fhs.append(spectator_fh)

        # --- Start replay client (player A) ---
        replay_log = game_dir / f"{player_a_name}_replay.log"
        replay_proc, replay_fh = _start_process(
            args=[
                sys.executable,
                "-m",
                "puppeteer.replay",
                "--server",
                server,
                "--port",
                str(port),
                "--username",
                player_a_name,
                "--project-root",
                str(project_root),
                "--deck",
                str(project_root / deck_a),
                "--script",
                str(script_path),
                "--game-dir",
                str(game_dir),
            ],
            cwd=project_root,
            env_updates={"PYTHONUNBUFFERED": "1"},
            log_path=replay_log,
        )
        procs.append(replay_proc)
        log_fhs.append(replay_fh)

        # --- Start potato client (player B) ---
        potato_log = game_dir / f"{player_b_name}_mcp.log"
        bridge_cp = compute_module_classpath(project_root, "Mage.Client.Bridge")
        potato_cmd = _build_java_cmd(
            bridge_cp,
            MAIN_CLASS_BRIDGE,
            {
                "xmage.bridge.server": server,
                "xmage.bridge.port": str(port),
                "xmage.bridge.personality": "potato",
                "xmage.bridge.username": player_b_name,
                "xmage.bridge.deck": str(project_root / deck_b),
            },
        )
        potato_proc, potato_fh = _start_process(
            args=potato_cmd,
            cwd=project_root / "Mage.Client.Bridge",
            env_updates={},
            log_path=potato_log,
        )
        procs.append(potato_proc)
        log_fhs.append(potato_fh)

        # Wait for the replay client to finish
        with timed_phase(golden_name, "replay_and_game"):
            try:
                replay_proc.wait(timeout=180)
            except subprocess.TimeoutExpired as e:
                log_text = spectator_log.read_text() if spectator_log.exists() else "<no log>"
                replay_text = replay_log.read_text() if replay_log.exists() else "<no log>"
                potato_text = potato_log.read_text() if potato_log.exists() else "<no log>"
                raise TimeoutError(
                    f"Replay client did not complete within 180s.\n"
                    f"Spectator log tail:\n{log_text[-2000:]}\n"
                    f"Replay log tail:\n{replay_text[-2000:]}\n"
                    f"Potato log tail:\n{potato_text[-2000:]}"
                ) from e

            if replay_proc.returncode != 0:
                replay_text = replay_log.read_text() if replay_log.exists() else "<no log>"
                raise RuntimeError(
                    f"Replay client exited with code {replay_proc.returncode}.\nReplay log tail:\n{replay_text[-2000:]}"
                )

        with timed_phase(golden_name, "file_quiescence"):
            _wait_for_files_quiescent([game_dir / "game_events.jsonl", game_dir / "server_game_events.jsonl"])

        prompt_path = game_dir / f"{player_a_name}_golden_prompt.json"
        assert prompt_path.exists(), f"Golden prompt not written: {prompt_path}\nCheck replay log: {replay_log}"
        prompt = json.loads(prompt_path.read_text())

        with timed_phase(golden_name, "golden_comparison"):
            assert_golden_prompt(golden_name, prompt)
            assert_golden_export(golden_name, game_dir)
            assert_golden_blunder_prompts(golden_name, game_dir, script)

        return prompt

    finally:
        for proc in procs:
            if proc.poll() is None:
                kill_tree(proc.pid)
        for fh in log_fhs:
            fh.close()


def run_golden_scenario_two_replay(
    server: str,
    port: int,
    project_root: Path,
    game_dir: Path,
    deck_a: str,
    deck_b: str,
    script_a: list[dict],
    script_b: list[dict],
    golden_name: str,
    player_a_name: str = "TestPlayer",
    player_b_name: str = "Opponent",
    game_type: str = "Two Player Duel",
    deck_type: str = "Constructed - Legacy",
    spectator: SpectatorProcess | None = None,
) -> list[dict]:
    """Run a golden test scenario with replay clients for both players.

    When ``spectator`` is provided, reuses the session-scoped spectator JVM
    instead of spawning a fresh one per test.

    Automatically asserts golden prompt and export comparisons using
    ``golden_name`` as the file identifier.
    """
    game_dir.mkdir(parents=True, exist_ok=True)

    # Write game metadata so build_export finds harness_epoch and player info
    meta = {
        "harness_epoch": HARNESS_EPOCH,
        "game_type": game_type,
        "deck_type": deck_type,
        "players": [
            {"type": "replay", "name": player_a_name, "deck_path": deck_a},
            {"type": "replay", "name": player_b_name, "deck_path": deck_b},
        ],
    }
    (game_dir / "game_meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    # Write scripts
    script_a_path = game_dir / "script_a.json"
    script_b_path = game_dir / "script_b.json"
    script_a_path.write_text(json.dumps(script_a))
    script_b_path.write_text(json.dumps(script_b))

    procs: list[subprocess.Popen] = []
    log_fhs: list = []

    try:
        # --- Start observer spectator ---
        if spectator is not None:
            spectator_log = spectator.log_path
            with timed_phase(golden_name, "spectator_command"):
                _send_spectator_command(
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
        else:
            with timed_phase(golden_name, "spectator_startup"):
                spectator_proc, spectator_fh, spectator_log = _start_spectator(
                    server,
                    port,
                    project_root,
                    game_dir,
                    deck_a,
                    deck_b,
                    player_a_name,
                    player_b_name,
                    "replay",
                    game_type,
                    deck_type,
                )
                procs.append(spectator_proc)
                log_fhs.append(spectator_fh)

        # --- Start replay client (player A) ---
        replay_a_log = game_dir / f"{player_a_name}_replay.log"
        replay_a_proc, replay_a_fh = _start_process(
            args=[
                sys.executable,
                "-m",
                "puppeteer.replay",
                "--server",
                server,
                "--port",
                str(port),
                "--username",
                player_a_name,
                "--project-root",
                str(project_root),
                "--deck",
                str(project_root / deck_a),
                "--script",
                str(script_a_path),
                "--game-dir",
                str(game_dir),
            ],
            cwd=project_root,
            env_updates={"PYTHONUNBUFFERED": "1"},
            log_path=replay_a_log,
        )
        procs.append(replay_a_proc)
        log_fhs.append(replay_a_fh)

        # --- Start replay client (player B) ---
        replay_b_log = game_dir / f"{player_b_name}_replay.log"
        replay_b_proc, replay_b_fh = _start_process(
            args=[
                sys.executable,
                "-m",
                "puppeteer.replay",
                "--server",
                server,
                "--port",
                str(port),
                "--username",
                player_b_name,
                "--project-root",
                str(project_root),
                "--deck",
                str(project_root / deck_b),
                "--script",
                str(script_b_path),
                "--game-dir",
                str(game_dir),
            ],
            cwd=project_root,
            env_updates={"PYTHONUNBUFFERED": "1"},
            log_path=replay_b_log,
        )
        procs.append(replay_b_proc)
        log_fhs.append(replay_b_fh)

        # Wait for replay clients to finish (they write golden prompts then concede)
        with timed_phase(golden_name, "replay_and_game"):
            try:
                replay_a_proc.wait(timeout=180)
                replay_b_proc.wait(timeout=180)
            except subprocess.TimeoutExpired as e:
                log_text = spectator_log.read_text() if spectator_log.exists() else "<no log>"
                replay_a_text = replay_a_log.read_text() if replay_a_log.exists() else "<no log>"
                replay_b_text = replay_b_log.read_text() if replay_b_log.exists() else "<no log>"
                raise TimeoutError(
                    "Replay clients did not complete within 180s.\n"
                    f"Spectator log tail:\n{log_text[-2000:]}\n"
                    f"Replay A log tail:\n{replay_a_text[-2000:]}\n"
                    f"Replay B log tail:\n{replay_b_text[-2000:]}"
                ) from e

            # Check replay client exit codes
            if replay_a_proc.returncode != 0:
                replay_a_text = replay_a_log.read_text() if replay_a_log.exists() else "<no log>"
                raise RuntimeError(
                    f"Replay client A exited with code {replay_a_proc.returncode}.\n"
                    f"Replay log tail:\n{replay_a_text[-2000:]}"
                )
            if replay_b_proc.returncode != 0:
                replay_b_text = replay_b_log.read_text() if replay_b_log.exists() else "<no log>"
                raise RuntimeError(
                    f"Replay client B exited with code {replay_b_proc.returncode}.\n"
                    f"Replay log tail:\n{replay_b_text[-2000:]}"
                )

        # Ensure spectator has finished flushing export inputs.
        with timed_phase(golden_name, "file_quiescence"):
            _wait_for_files_quiescent([game_dir / "game_events.jsonl", game_dir / "server_game_events.jsonl"])

        # Read golden prompt for player A
        prompt_path = game_dir / f"{player_a_name}_golden_prompt.json"
        assert prompt_path.exists(), f"Golden prompt not written: {prompt_path}\nCheck replay log: {replay_a_log}"
        prompt = json.loads(prompt_path.read_text())

        with timed_phase(golden_name, "golden_comparison"):
            assert_golden_prompt(golden_name, prompt)
            assert_golden_export(golden_name, game_dir)

        return prompt

    finally:
        for proc in procs:
            if proc.poll() is None:
                kill_tree(proc.pid)
        for fh in log_fhs:
            fh.close()


def _to_sorted_json(obj: object) -> str:
    """Deterministic JSON serialization with sorted keys."""
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)


def _brief(value: object, max_len: int = 80) -> str:
    """Short representation of a JSON value for diff output."""
    if isinstance(value, str):
        r = repr(value)
        if len(r) > max_len:
            return r[: max_len - 3] + "..."
        return r
    s = json.dumps(value, sort_keys=True, ensure_ascii=False)
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


def _is_short_id(value: object) -> bool:
    return isinstance(value, str) and len(value) > 1 and value[0] in ("p", "l") and value[1:].isdigit()


def _normalize_prompt_for_golden(obj: object) -> object:
    """Normalize prompt payloads for deterministic golden comparisons.

    - Strip short IDs (pN) to avoid non-semantic ID churn.
    - Parse embedded JSON strings and re-serialize with sorted keys.
    """
    if isinstance(obj, dict):
        out: dict[str, object] = {}
        for key, value in obj.items():
            if key == "id" and _is_short_id(value):
                out[key] = "_"
                continue
            out[key] = _normalize_prompt_for_golden(value)
        return out
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

    assert golden_file.exists(), f"Golden file not found: {golden_file}\nRun 'make update-golden' to generate it."

    expected = golden_file.read_text().rstrip()
    if expected != actual_json:
        expected_obj = json.loads(expected)
        actual_obj = json.loads(actual_json)
        diff_lines = _json_diff(expected_obj, actual_obj)
        diff_text = "\n".join(diff_lines)
        raise AssertionError(
            f"Golden file mismatch: {name}.json\nRun 'make update-golden' to regenerate.\n\n{diff_text}"
        )


def _normalize_embedded_json(obj: object) -> object:
    """Recursively normalize embedded JSON strings for deterministic key order.

    MCP tool results are serialized as JSON strings within the export data.
    The key order in these strings can vary between runs (e.g. {"blocks":"p10","id":"p7"}
    vs {"id":"p7","blocks":"p10"}). Parse and re-serialize with sorted keys.
    """
    if isinstance(obj, dict):
        return {k: _normalize_embedded_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_normalize_embedded_json(item) for item in obj]
    elif isinstance(obj, str) and obj.startswith(("{", "[")):
        try:
            parsed = json.loads(obj)
            return _normalize_embedded_json(parsed)
        except (json.JSONDecodeError, ValueError):
            return obj
    return obj


def _strip_volatile(data: dict) -> None:
    """Remove fields that vary between test runs from export data, in place."""
    # Top-level volatile fields
    data.pop("timestamp", None)
    data.pop("id", None)
    data.pop("harnessEpoch", None)
    # Error log entries contain wall-clock timestamps in message text
    data.pop("errors", None)

    # Strip volatile fields from player summaries
    for player in data.get("players", []):
        player.pop("thinkingTimeSecs", None)

    # Strip ts from actions
    for action in data.get("actions", []):
        action.pop("ts", None)

    # Sort llmEvents by (seq, player) then strip ts.
    # seq-first keeps events interleaved chronologically across players;
    # player breaks ties deterministically (ts is stripped as volatile,
    # so it can't be a sort key — wall-clock order varies between runs).
    for event in data.get("llmEvents", []):
        event.pop("ts", None)
    data.get("llmEvents", []).sort(key=lambda e: (e.get("seq", 0), e.get("player", "")))

    # Same for llmTrace.
    for event in data.get("llmTrace", []):
        event.pop("ts", None)
    data.get("llmTrace", []).sort(key=lambda e: (e.get("seq", 0), e.get("player", "")))


def assert_golden_export(name: str, game_dir: Path) -> None:
    """Run export pipeline on game dir, compare against golden file."""
    # Import here to avoid circular imports at module level
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from export_game import build_export

    export_data = build_export(game_dir)
    _strip_volatile(export_data)
    export_data = _normalize_embedded_json(export_data)
    actual_json = _to_sorted_json(export_data)
    golden_file = GOLDEN_EXPORTS_DIR / f"{name}.json"

    if UPDATE_MODE:
        golden_file.parent.mkdir(parents=True, exist_ok=True)
        golden_file.write_text(actual_json + "\n")
        print(f"Updated golden export: {golden_file}")
        return

    assert golden_file.exists(), (
        f"Golden export file not found: {golden_file}\nRun 'make update-golden' to generate it."
    )

    expected = golden_file.read_text().rstrip()
    if expected != actual_json:
        expected_obj = json.loads(expected)
        diff_lines = _json_diff(expected_obj, export_data)
        diff_text = "\n".join(diff_lines)
        raise AssertionError(
            f"Golden export mismatch: {name}.json\nRun 'make update-golden' to regenerate.\n\n{diff_text}"
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


def assert_golden_blunder_prompts(name: str, game_dir: Path, script: list[dict]) -> None:
    """Check blunder analysis prompts for script steps annotated with ``golden_blunder``.

    For each annotated ``choose_action`` in the script, builds the blunder
    evaluation prompt (system + user) from the game export and compares against
    golden reference files.  Skips entirely if no script steps are annotated.
    """
    annotated = _script_blunder_indices(script)
    if not annotated:
        return

    # Late imports — same pattern as assert_golden_export
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "analysis"))
    from blunder_analysis import _actions_by_turn, _game_overview, build_decision_prompt
    from blunder_eval_common import decision_index
    from export_game import build_export
    from extract_decisions import extract_decisions

    # Build full (unstripped) export and extract decisions
    export_data = build_export(game_dir)
    tmp_export = game_dir / "_blunder_export.json"
    tmp_export.write_text(json.dumps(export_data))
    try:
        decisions = extract_decisions(str(tmp_export))
    finally:
        tmp_export.unlink()

    # Build prompt context
    overview = _game_overview(export_data)
    snapshots = export_data.get("snapshots", [])
    actions = export_data.get("actions", [])
    abt = _actions_by_turn(actions)
    num_players = len(export_data.get("players", []))

    # Oracle cache: load from golden dir, or generate in update mode
    golden_dir = GOLDEN_BLUNDER_DIR / name
    oracle_cache_path = golden_dir / "oracle_cache.json"

    if UPDATE_MODE:
        from blunder_analysis import _collect_card_names, _get_oracle_texts

        all_names = _collect_card_names(export_data)
        oracle_texts = _get_oracle_texts(sorted(all_names))
        golden_dir.mkdir(parents=True, exist_ok=True)
        oracle_cache_path.write_text(json.dumps(oracle_texts, indent=2, sort_keys=True) + "\n")
    else:
        assert oracle_cache_path.exists(), (
            f"Oracle cache missing: {oracle_cache_path}\nRun 'make update-golden' to generate."
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
            f"Golden blunder prompt missing: {golden_file}\nRun 'make update-golden' to generate."
        )
        expected = json.loads(golden_file.read_text())

        if actual["system"] != expected["system"]:
            raise AssertionError(
                f"Blunder system prompt changed for {name} decision {idx}\nRun 'make update-golden' to regenerate."
            )
        if actual["user"] != expected["user"]:
            raise AssertionError(
                f"Blunder user message changed for {name} decision {idx}\nRun 'make update-golden' to regenerate."
            )
