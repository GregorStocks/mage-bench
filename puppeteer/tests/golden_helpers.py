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

        # Wait for spectator to flush game_events.jsonl (the spectator writes
        # events incrementally but Java IO buffering means short games may not
        # have flushed yet when the replay client exits).
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
    """Recursively strip timestamp fields from export data for stable comparison."""
    if isinstance(obj, dict):
        return {k: _strip_volatile_fields(v) for k, v in obj.items() if k != "ts"}
    if isinstance(obj, list):
        return [_strip_volatile_fields(item) for item in obj]
    return obj


def assert_golden_export(name: str, game_dir: Path, prompt: list[dict]) -> None:
    """Compare a pre-generated .json.gz export against the golden file.

    The export is generated inside run_golden_scenario() (while the spectator
    is still alive) and stored in game_dir/_export_tmp/. This function reads
    that export, augments it with goldenFixture data, and either updates the
    golden file (UPDATE_GOLDEN mode) or compares against it.
    """
    # Find the pre-generated export
    export_dir = game_dir / "_export_tmp"
    exports = list(export_dir.glob("*.json.gz")) if export_dir.exists() else []
    if not exports:
        # Spectator didn't write game_events.jsonl (very short game).
        # Skip export assertion — the golden prompt test already validates
        # game state correctness.
        print(f"  Skipping golden export for {name}: no game_events.jsonl from spectator")
        return
    export_path = exports[0]

    # Read and augment with golden fixture data
    export_data = json.loads(gzip.decompress(export_path.read_bytes()))
    export_data["goldenFixture"] = _extract_mcp_fixture(prompt)

    # Strip volatile fields (timestamps) for deterministic comparison
    stable = _strip_volatile_fields(export_data)
    actual_json = _to_sorted_json(stable)

    golden_file = GOLDEN_EXPORTS_DIR / f"{name}.json.gz"

    if UPDATE_MODE:
        GOLDEN_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        golden_file.write_bytes(gzip.compress((actual_json + "\n").encode()))
        print(f"Updated golden export: {golden_file}")
        return

    if not golden_file.exists():
        # Golden exports haven't been generated yet. Run 'make update-golden'
        # to generate them. Skip comparison for now.
        print(f"  Golden export not found: {golden_file.name} (run 'make update-golden' to generate)")
        return

    expected_json = gzip.decompress(golden_file.read_bytes()).decode().rstrip()
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
