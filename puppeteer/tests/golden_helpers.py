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
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from puppeteer.harness_epoch import HARNESS_EPOCH
from puppeteer.process_manager import kill_tree

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "golden" / "prompts"
GOLDEN_EXPORTS_DIR = Path(__file__).resolve().parent / "golden" / "exports"

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


# ---------------------------------------------------------------------------
# Persistent process wrappers for session-scoped JVM reuse
# ---------------------------------------------------------------------------


class BridgeSession:
    """Persistent MCP bridge JVM accessed via direct JSON-RPC over stdin/stdout.

    Avoids the MCP SDK's subprocess management so we can keep the JVM alive
    across multiple golden tests.
    """

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self.proc = proc
        self._id = 0
        assert proc.stdin is not None, "BridgeSession requires stdin=PIPE"
        assert proc.stdout is not None, "BridgeSession requires stdout=PIPE"
        self._stdin = io.TextIOWrapper(proc.stdin, encoding="utf-8", line_buffering=True)
        self._stdout = io.TextIOWrapper(proc.stdout, encoding="utf-8")

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        req: dict = {"jsonrpc": "2.0", "method": method, "id": self._id}
        if params is not None:
            req["params"] = params
        line = json.dumps(req, separators=(",", ":"))
        self._stdin.write(line + "\n")
        self._stdin.flush()
        resp_line = self._stdout.readline()
        assert resp_line, "Bridge process closed stdout unexpectedly"
        resp = json.loads(resp_line)
        if "error" in resp and resp["error"] is not None:
            raise RuntimeError(f"MCP error: {resp['error']}")
        return resp["result"]

    def initialize(self) -> dict:
        return self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})

    def list_tools(self) -> list[str]:
        """Return names of available MCP tools."""
        result = self._rpc("tools/list", {})
        return [t["name"] for t in result["tools"]]

    def call_tool(self, name: str, arguments: dict | None = None) -> str:
        """Call an MCP tool and return the result text (matches execute_tool() return format)."""
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        return result["content"][0]["text"]

    def close(self) -> None:
        try:
            self._stdin.close()
        except Exception:
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

        # Concede to end the game
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
    raise TimeoutError(f"No game_end event found within {timeout}s in {game_dir}")


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
) -> list[dict]:
    """Run a golden test scenario with a replay player vs a potato opponent.

    Starts a streaming spectator (creates the game table), a replay client
    (executes scripted MCP tool calls and captures the LLM prompt), and a
    potato client (auto-responds to everything as the opponent).

    When ``bridge`` and/or ``potato`` are provided, reuses those session-scoped
    JVM processes instead of spawning fresh ones per test.

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
    """Start a streaming spectator and wait for table creation.

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

    jvm_opens = "--add-opens=java.base/java.io=ALL-UNNAMED"
    jvm_no_ui = jvm_opens
    if sys.platform == "darwin":
        jvm_no_ui += " -Dapple.awt.UIElement=true"

    spectator_log = game_dir / "spectator.log"
    spectator_jvm = " ".join(
        [
            jvm_no_ui,
            "-Dxmage.aiPuppeteer.autoConnect=true",
            "-Dxmage.aiPuppeteer.autoStart=true",
            "-Dxmage.aiPuppeteer.disableWhatsNew=true",
            "-Dxmage.streaming.noWindow=true",
            f"-Dxmage.aiPuppeteer.server={server}",
            f"-Dxmage.aiPuppeteer.port={port}",
            "-Dxmage.aiPuppeteer.user=spectator",
            "-Dxmage.aiPuppeteer.password=",
            f"-Dxmage.streaming.gameDir={game_dir}",
        ]
    )

    spectator_proc, spectator_fh = _start_process(
        args=["mvn", "-q", "exec:java"],
        cwd=project_root / "Mage.Client.Streaming",
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
            "MAVEN_OPTS": spectator_jvm,
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
        # Start spectator (always per-test)
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
        bridge.call_tool("join_table", {"deck_path": str(project_root / deck_a)})

        # Execute replay script on the persistent bridge
        prompt = _run_replay_on_bridge(bridge, script, game_dir, player_a_name)

        # Wait for the spectator to record the game_end event before checking
        # file quiescence — otherwise the files may appear "stable" before
        # the game_end event is written, producing gameOver=null in the export.
        _wait_for_game_end_event(game_dir)
        _wait_for_files_quiescent([game_dir / "game_events.jsonl", game_dir / "server_game_events.jsonl"])

        assert_golden_prompt(golden_name, prompt)
        assert_golden_export(golden_name, game_dir)

        return prompt

    finally:
        # Kill only the per-test spectator — bridge and potato are session-scoped
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

    # JVM options
    jvm_opens = "--add-opens=java.base/java.io=ALL-UNNAMED"
    jvm_no_ui = jvm_opens
    if sys.platform == "darwin":
        jvm_no_ui += " -Dapple.awt.UIElement=true"

    procs: list[subprocess.Popen] = []
    log_fhs: list = []

    try:
        # Start spectator
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
        potato_jvm = " ".join(
            [
                jvm_no_ui,
                f"-Dxmage.bridge.server={server}",
                f"-Dxmage.bridge.port={port}",
                "-Dxmage.bridge.personality=potato",
            ]
        )
        potato_proc, potato_fh = _start_process(
            args=[
                "mvn",
                "-q",
                f"-Dxmage.bridge.username={player_b_name}",
                f"-Dxmage.bridge.deck={project_root / deck_b}",
                "exec:java",
            ],
            cwd=project_root / "Mage.Client.Bridge",
            env_updates={"MAVEN_OPTS": potato_jvm},
            log_path=potato_log,
        )
        procs.append(potato_proc)
        log_fhs.append(potato_fh)

        # Wait for the replay client to finish
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

        _wait_for_files_quiescent([game_dir / "game_events.jsonl", game_dir / "server_game_events.jsonl"])

        prompt_path = game_dir / f"{player_a_name}_golden_prompt.json"
        assert prompt_path.exists(), f"Golden prompt not written: {prompt_path}\nCheck replay log: {replay_log}"
        prompt = json.loads(prompt_path.read_text())

        assert_golden_prompt(golden_name, prompt)
        assert_golden_export(golden_name, game_dir)

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
) -> list[dict]:
    """Run a golden test scenario with replay clients for both players.

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

    # Build player config JSON for the spectator
    players_config = json.dumps(
        {
            "players": [
                {"type": "replay", "name": player_a_name, "deck": deck_a},
                {"type": "replay", "name": player_b_name, "deck": deck_b},
            ],
            "gameType": game_type,
            "deckType": deck_type,
        },
        separators=(",", ":"),
    )

    # JVM options
    jvm_opens = "--add-opens=java.base/java.io=ALL-UNNAMED"
    jvm_no_ui = jvm_opens
    if sys.platform == "darwin":
        jvm_no_ui += " -Dapple.awt.UIElement=true"

    procs: list[subprocess.Popen] = []
    log_fhs: list = []

    try:
        # --- Start streaming spectator ---
        spectator_log = game_dir / "spectator.log"
        spectator_jvm = " ".join(
            [
                jvm_no_ui,
                "-Dxmage.aiPuppeteer.autoConnect=true",
                "-Dxmage.aiPuppeteer.autoStart=true",
                "-Dxmage.aiPuppeteer.disableWhatsNew=true",
                "-Dxmage.streaming.noWindow=true",
                f"-Dxmage.aiPuppeteer.server={server}",
                f"-Dxmage.aiPuppeteer.port={port}",
                "-Dxmage.aiPuppeteer.user=spectator",
                "-Dxmage.aiPuppeteer.password=",
                f"-Dxmage.streaming.gameDir={game_dir}",
            ]
        )

        spectator_proc, spectator_fh = _start_process(
            args=["mvn", "-q", "exec:java"],
            cwd=project_root / "Mage.Client.Streaming",
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
                "MAVEN_OPTS": spectator_jvm,
            },
            log_path=spectator_log,
        )
        procs.append(spectator_proc)
        log_fhs.append(spectator_fh)

        # Wait for table creation
        _wait_for_log_marker(
            spectator_log,
            "AI Puppeteer: waiting for",
            spectator_proc,
            timeout=SPECTATOR_READY_TIMEOUT_SECONDS,
        )

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
        _wait_for_files_quiescent([game_dir / "game_events.jsonl", game_dir / "server_game_events.jsonl"])

        # Read golden prompt for player A
        prompt_path = game_dir / f"{player_a_name}_golden_prompt.json"
        assert prompt_path.exists(), f"Golden prompt not written: {prompt_path}\nCheck replay log: {replay_a_log}"
        prompt = json.loads(prompt_path.read_text())

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


def _is_short_id(value: object) -> bool:
    return isinstance(value, str) and len(value) > 1 and value[0] == "p" and value[1:].isdigit()


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
        expected_lines = expected.split("\n")
        actual_lines = actual_json.split("\n")
        diffs = []
        max_lines = max(len(expected_lines), len(actual_lines))
        for i in range(max_lines):
            exp = expected_lines[i] if i < len(expected_lines) else "<missing>"
            act = actual_lines[i] if i < len(actual_lines) else "<missing>"
            if exp != act:
                diffs.append(f"  Line {i + 1}:\n    expected: {exp}\n    actual:   {act}")
        diff_text = "\n".join(diffs[:20])
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

    # Strip volatile fields from player summaries
    for player in data.get("players", []):
        player.pop("thinkingTimeSecs", None)

    # Strip ts from actions
    for action in data.get("actions", []):
        action.pop("ts", None)

    # Strip ts from llmEvents, then sort deterministically.
    # Events from different players can interleave with sub-millisecond
    # timestamp differences, so the sort order is fragile across runs.
    for event in data.get("llmEvents", []):
        event.pop("ts", None)
    data.get("llmEvents", []).sort(key=lambda e: json.dumps(e, sort_keys=True, ensure_ascii=False))

    # Strip ts from llmTrace and sort deterministically.
    for event in data.get("llmTrace", []):
        event.pop("ts", None)
    data.get("llmTrace", []).sort(key=lambda e: json.dumps(e, sort_keys=True, ensure_ascii=False))


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
        expected_lines = expected.split("\n")
        actual_lines = actual_json.split("\n")
        diffs = []
        max_lines = max(len(expected_lines), len(actual_lines))
        for i in range(max_lines):
            exp = expected_lines[i] if i < len(expected_lines) else "<missing>"
            act = actual_lines[i] if i < len(actual_lines) else "<missing>"
            if exp != act:
                diffs.append(f"  Line {i + 1}:\n    expected: {exp}\n    actual:   {act}")
        diff_text = "\n".join(diffs[:20])
        raise AssertionError(
            f"Golden export mismatch: {name}.json\nRun 'make update-golden' to regenerate.\n\n{diff_text}"
        )
