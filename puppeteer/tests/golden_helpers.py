"""Shared helpers for golden prompt integration tests.

Runs real XMage games with scripted replay pilots, captures the exact
messages array that would be sent to the LLM, and compares against golden files.

These are integration tests that require compilation and a running XMage server.
They are NOT included in ``make test`` — run them with ``make test-golden``.

To run:    make test-golden
To update: make update-golden
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from puppeteer.process_manager import kill_tree

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "golden" / "prompts"

UPDATE_MODE = os.environ.get("UPDATE_GOLDEN", "").lower() in ("1", "true", "yes")

# Default decks for tests (relative to project root)
DECK_RED_STOMPY = "Mage.Client/release/sample-decks/Legacy/Red-Stompy.dck"
DECK_GOBLINS = "Mage.Client/release/sample-decks/Legacy/Goblins.dck"

# Custom test decks (relative to project root)
DECK_BOLT_AND_BURN = "puppeteer/tests/decks/bolt_and_burn.dck"
DECK_CLONE_AND_MEMNITE = "puppeteer/tests/decks/clone_and_memnite.dck"
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


def assert_golden_prompt(name: str, actual: list[dict]) -> None:
    """Compare prompt messages against golden file, or update in UPDATE_GOLDEN mode."""
    actual_json = _to_sorted_json(actual)
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
