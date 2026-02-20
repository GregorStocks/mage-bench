"""Shared helpers for golden prompt integration tests.

Runs real XMage games with scripted replay pilots, captures the exact
messages array that would be sent to the LLM, and compares against golden files.

These are integration tests that require compilation and a running XMage server.
They are NOT included in ``make test`` — run them with ``make test-golden``.

To run:    make test-golden
To update: make update-golden
"""

import gzip
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from puppeteer.game_log import read_decklist
from puppeteer.process_manager import kill_tree

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "golden" / "prompts"
GOLDEN_EXPORTS_DIR = Path(__file__).resolve().parent / "golden" / "exports"

UPDATE_MODE = os.environ.get("UPDATE_GOLDEN", "").lower() in ("1", "true", "yes")

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


def _wait_for_marker_file(
    marker_path: Path,
    proc: subprocess.Popen,
    log_path: Path,
    timeout: int = 60,
) -> None:
    """Wait for a marker file to appear, or fail fast if the process exits."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker_path.exists():
            return
        if proc.poll() is not None:
            log_text = log_path.read_text() if log_path.exists() else "<no log>"
            raise RuntimeError(
                f"Process exited (rc={proc.returncode}) before marker appeared.\n"
                f"Marker: {marker_path}\n"
                f"Log tail:\n{log_text[-2000:]}"
            )
        time.sleep(0.5)
    log_text = log_path.read_text() if log_path.exists() else "<no log>"
    raise TimeoutError(f"Marker file not found within {timeout}s: {marker_path}\nLog tail:\n{log_text[-2000:]}")


def run_golden_scenario(
    server: str,
    port: int,
    project_root: Path,
    game_dir: Path,
    deck_a: str,
    deck_b: str,
    script: list[dict],
    player_a_name: str = "TestPlayer",
    player_b_name: str = "Opponent",
    game_type: str = "Two Player Duel",
    deck_type: str = "Constructed - Legacy",
) -> list[dict]:
    """Run a golden test scenario with a replay player vs a potato opponent.

    Starts a streaming spectator (creates the game table), a replay client
    (executes scripted MCP tool calls and captures the LLM prompt), and a
    potato client (auto-responds to everything as the opponent).

    Returns the captured prompt messages array (what the LLM would see).
    """
    game_dir.mkdir(parents=True, exist_ok=True)

    # Write script file
    script_path = game_dir / "script.json"
    script_path.write_text(json.dumps(script))

    # Build player config JSON for the spectator
    players_config = json.dumps(
        {
            "players": [
                {"type": "replay", "name": player_a_name, "deck": deck_a},
                {"type": "potato", "name": player_b_name, "deck": deck_b},
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
        _wait_for_log_marker(spectator_log, "AI Puppeteer: waiting for", spectator_proc, timeout=120)

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
                f"-Dxmage.headless.server={server}",
                f"-Dxmage.headless.port={port}",
                "-Dxmage.headless.personality=potato",
            ]
        )
        potato_proc, potato_fh = _start_process(
            args=[
                "mvn",
                "-q",
                f"-Dxmage.headless.username={player_b_name}",
                f"-Dxmage.headless.deck={project_root / deck_b}",
                f"-Dxmage.headless.gameDir={game_dir}",
                "exec:java",
            ],
            cwd=project_root / "Mage.Client.Headless",
            env_updates={"MAVEN_OPTS": potato_jvm},
            log_path=potato_log,
        )
        procs.append(potato_proc)
        log_fhs.append(potato_fh)

        # Wait for the replay client to finish (it writes the golden prompt
        # then concedes and exits). The spectator doesn't reliably receive
        # game_over when watching very short games, so we don't wait for it.
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

        # Check replay client exit code
        if replay_proc.returncode != 0:
            replay_text = replay_log.read_text() if replay_log.exists() else "<no log>"
            raise RuntimeError(
                f"Replay client exited with code {replay_proc.returncode}.\nReplay log tail:\n{replay_text[-2000:]}"
            )

        # Wait for the spectator to finish processing events and flush the log.
        _wait_for_marker_file(game_dir / "observer_done", spectator_proc, spectator_log, timeout=60)

        # Ensure game_events.jsonl exists before export.
        events_path = game_dir / "game_events.jsonl"
        flush_deadline = time.monotonic() + 10
        while not events_path.exists() and time.monotonic() < flush_deadline:
            time.sleep(0.5)

        # Write minimal game_meta.json for export_game() to produce card images
        meta = {
            "game_type": game_type,
            "deck_type": deck_type,
            "players": [
                {
                    "name": player_a_name,
                    "type": "replay",
                    "deck_path": deck_a,
                    "decklist": read_decklist(project_root / deck_a),
                },
                {
                    "name": player_b_name,
                    "type": "potato",
                    "deck_path": deck_b,
                    "decklist": read_decklist(project_root / deck_b),
                },
            ],
        }
        (game_dir / "game_meta.json").write_text(json.dumps(meta, indent=2) + "\n")

        # Generate the game export while the spectator is still alive
        # (game_events.jsonl must be read before processes are killed).
        if events_path.exists():
            sys.path.insert(0, str(REPO_ROOT / "scripts"))
            from export_game import export_game

            export_dir = game_dir / "_export_tmp"
            export_dir.mkdir(exist_ok=True)
            export_game(game_dir, export_dir)

        # Read golden prompt
        prompt_path = game_dir / f"{player_a_name}_golden_prompt.json"
        assert prompt_path.exists(), f"Golden prompt not written: {prompt_path}\nCheck replay log: {replay_log}"
        return json.loads(prompt_path.read_text())

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
    player_a_name: str = "TestPlayer",
    player_b_name: str = "Opponent",
    game_type: str = "Two Player Duel",
    deck_type: str = "Constructed - Legacy",
) -> list[dict]:
    """Run a golden test scenario with replay clients for both players."""
    game_dir.mkdir(parents=True, exist_ok=True)

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
        _wait_for_log_marker(spectator_log, "AI Puppeteer: waiting for", spectator_proc, timeout=120)

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

        # Wait for the spectator to finish processing events and flush the log.
        _wait_for_marker_file(game_dir / "observer_done", spectator_proc, spectator_log, timeout=60)

        # Wait for spectator to flush game_events.jsonl
        events_path = game_dir / "game_events.jsonl"
        flush_deadline = time.monotonic() + 10
        while not events_path.exists() and time.monotonic() < flush_deadline:
            time.sleep(0.5)

        # Write minimal game_meta.json for export_game()
        meta = {
            "game_type": game_type,
            "deck_type": deck_type,
            "players": [
                {
                    "name": player_a_name,
                    "type": "replay",
                    "deck_path": deck_a,
                    "decklist": read_decklist(project_root / deck_a),
                },
                {
                    "name": player_b_name,
                    "type": "replay",
                    "deck_path": deck_b,
                    "decklist": read_decklist(project_root / deck_b),
                },
            ],
        }
        (game_dir / "game_meta.json").write_text(json.dumps(meta, indent=2) + "\n")

        # Generate the game export while the spectator is still alive
        if events_path.exists():
            sys.path.insert(0, str(REPO_ROOT / "scripts"))
            from export_game import export_game

            export_dir = game_dir / "_export_tmp"
            export_dir.mkdir(exist_ok=True)
            export_game(game_dir, export_dir)

        # Read golden prompt for player A
        prompt_path = game_dir / f"{player_a_name}_golden_prompt.json"
        assert prompt_path.exists(), f"Golden prompt not written: {prompt_path}\nCheck replay log: {replay_a_log}"
        return json.loads(prompt_path.read_text())

    finally:
        for proc in procs:
            if proc.poll() is None:
                kill_tree(proc.pid)
        for fh in log_fhs:
            fh.close()


def _to_sorted_json(obj: object) -> str:
    """Deterministic JSON serialization with sorted keys."""
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)


def _assert_golden_match(name: str, actual_json: str, golden_file: Path) -> None:
    """Compare actual JSON against a golden file, with diff on mismatch."""
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
            f"Golden file mismatch: {golden_file.name}\nRun 'make update-golden' to regenerate.\n\n{diff_text}"
        )


def assert_golden_prompt(name: str, actual: list[dict]) -> None:
    """Compare prompt messages against golden file, or update in UPDATE_GOLDEN mode."""
    actual_json = _to_sorted_json(actual)
    golden_file = GOLDEN_DIR / f"{name}.json"

    if UPDATE_MODE:
        golden_file.parent.mkdir(parents=True, exist_ok=True)
        golden_file.write_text(actual_json + "\n")
        print(f"Updated golden file: {golden_file}")
        return

    _assert_golden_match(name, actual_json, golden_file)


def _extract_mcp_fixture(prompt: list[dict]) -> dict:
    """Extract the last get_game_state and pass_priority results from a prompt."""
    # Build tool_call_id -> tool_name map
    tool_call_names: dict[str, str] = {}
    for msg in prompt:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tool_call_names[tc["id"]] = tc["function"]["name"]

    target_tools = {"get_game_state", "pass_priority"}
    game_state = None
    pass_priority = None
    for msg in prompt:
        if msg.get("role") != "tool":
            continue
        call_name = tool_call_names.get(msg.get("tool_call_id", ""))
        if call_name not in target_tools:
            continue
        content = msg["content"].lstrip()
        if not content.startswith("{"):
            continue
        parsed = json.loads(content)
        if call_name == "get_game_state":
            game_state = parsed
        elif call_name == "pass_priority":
            pass_priority = parsed

    return {"mcp_game_state": game_state, "mcp_pass_priority": pass_priority}


def _strip_volatile_fields(obj: object) -> object:
    """Recursively strip timestamp fields from export data."""
    if isinstance(obj, dict):
        return {k: _strip_volatile_fields(v) for k, v in obj.items() if k != "ts"}
    if isinstance(obj, list):
        return [_strip_volatile_fields(item) for item in obj]
    return obj


# Fields that are nondeterministic across runs but useful for the website.
# Stripped only for golden comparison, NOT from the saved golden file.
_NONDETERMINISTIC_KEYS = {"thinkingTimeSecs", "seq"}

# Connection-lifecycle action messages whose ordering depends on client
# connect/disconnect timing, not game state.  Stripped for comparison only.
_LIFECYCLE_SUFFIXES = (
    "has joined",
    "has joined the game",
    "has left XMage",
    "has started watching",
    "wants to concede",
    "has won the game",
)


def _is_lifecycle_action(action: object) -> bool:
    """Return True for join/leave/concede/win messages whose ordering is nondeterministic."""
    if not isinstance(action, dict):
        return False
    msg = action.get("message", "")
    if any(msg.endswith(s) for s in _LIFECYCLE_SUFFIXES):
        return True
    # Match score HTML contains player names in nondeterministic order
    return "Match score:" in msg


def _normalize_actions(actions: list) -> list:
    """Normalize actions for deterministic comparison.

    Strips lifecycle messages and sorts by message content (spectator event
    ordering can vary with concurrent clients). Seq is already stripped by
    _NONDETERMINISTIC_KEYS.
    """
    filtered = [a for a in actions if not _is_lifecycle_action(a)]
    filtered.sort(key=lambda a: a.get("message", "") if isinstance(a, dict) else "")
    return filtered


def _sort_players(players: list) -> list:
    """Sort players array by name for deterministic comparison."""
    return sorted(players, key=lambda p: p.get("name", "") if isinstance(p, dict) else "")


def _strip_nondeterministic_fields(obj: object) -> object:
    """Strip fields that vary across runs for deterministic golden comparison.

    - thinkingTimeSecs: computed from wall-clock timestamps
    - seq: shifts when lifecycle events change count
    - Connection-lifecycle action messages (join/leave/concede/win ordering)
    - Action ordering (varies with concurrent clients)
    - players array ordering (varies with client connect order)
    """
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if k in _NONDETERMINISTIC_KEYS:
                continue
            if k == "actions" and isinstance(v, list):
                result[k] = _normalize_actions([_strip_nondeterministic_fields(item) for item in v])
            elif k == "players" and isinstance(v, list):
                result[k] = _sort_players([_strip_nondeterministic_fields(item) for item in v])
            else:
                result[k] = _strip_nondeterministic_fields(v)
        return result
    if isinstance(obj, list):
        return [_strip_nondeterministic_fields(item) for item in obj]
    return obj


def assert_golden_export(name: str, game_dir: Path, prompt: list[dict]) -> None:
    """Compare a pre-generated .json.gz export against the golden file.

    The export is generated inside run_golden_scenario() (while the spectator
    is still alive) and stored in game_dir/_export_tmp/. This function reads
    that export, augments it with goldenFixture data, and either updates the
    golden file (UPDATE_GOLDEN mode) or compares against it.
    """
    # Find the pre-generated export (generated inside run_golden_scenario
    # while the spectator was still alive)
    export_dir = game_dir / "_export_tmp"
    exports = list(export_dir.glob("*.json.gz")) if export_dir.exists() else []
    assert exports, (
        f"No export generated for {name}: spectator didn't write game_events.jsonl.\n"
        f"Check spectator log: {game_dir / 'spectator.log'}"
    )
    export_path = exports[0]

    # Read and augment with golden fixture data
    export_data = json.loads(gzip.decompress(export_path.read_bytes()))
    export_data["goldenFixture"] = _extract_mcp_fixture(prompt)

    # Strip timestamps (not useful even for the website)
    stable = _strip_volatile_fields(export_data)

    golden_file = GOLDEN_EXPORTS_DIR / f"{name}.json.gz"

    if UPDATE_MODE:
        # Save full data (minus timestamps) so the website can use snapshots/seq
        GOLDEN_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        save_json = _to_sorted_json(stable)
        golden_file.write_bytes(gzip.compress((save_json + "\n").encode()))
        print(f"Updated golden export: {golden_file}")
        return

    assert golden_file.exists(), f"Golden export not found: {golden_file}\nRun 'make update-golden' to generate it."

    expected_data = json.loads(gzip.decompress(golden_file.read_bytes()))

    # Strip nondeterministic fields from both sides for comparison:
    # - thinkingTimeSecs: wall-clock timing
    # - join/leave action messages: ordering depends on client connect timing
    actual_json = _to_sorted_json(_strip_nondeterministic_fields(stable))
    expected_json = _to_sorted_json(_strip_nondeterministic_fields(expected_data))

    if expected_json != actual_json:
        expected_lines = expected_json.split("\n")
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
            f"Golden export mismatch: {name}.json.gz\nRun 'make update-golden' to regenerate.\n\n{diff_text}"
        )
