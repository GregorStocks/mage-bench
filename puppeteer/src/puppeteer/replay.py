"""Replay pilot: drives an XMage game with scripted MCP tool calls.

Instead of an LLM making decisions, the replay pilot executes a predefined
sequence of tool calls against the real MCP bridge. After the script is
exhausted, it captures the LLM prompt (what the LLM would see at that point)
and concedes the game.

Used by golden prompt tests to produce realistic game histories with
deterministic, reproducible tool call sequences.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from pathlib import Path

from puppeteer.config import load_prompts
from puppeteer.game_log import GameLogWriter
from puppeteer.log import get_logger, setup_logging
from puppeteer.pilot import BoardCursorTracker, _render_context, build_initial_message, execute_tool

logger = get_logger(__name__)


def _load_default_system_prompt() -> str:
    """Load the default system prompt from prompts.json."""
    prompts = load_prompts(None)
    assert "default" in prompts, "prompts.json must contain a 'default' key"
    return prompts["default"]


AsyncCallToolFn = Callable[[str, dict], Awaitable[str]]


async def execute_replay_script(
    call_tool: AsyncCallToolFn,
    script: list[dict],
    system_prompt: str,
    game_log: GameLogWriter | None = None,
) -> list[dict]:
    """Execute a replay script and return the captured prompt messages.

    This is the shared core of both ``run_replay`` (async MCP subprocess) and
    the persistent-session path in golden_helpers. The ``call_tool`` callable
    abstracts over the transport — async MCP SDK or sync JSON-RPC (wrapped).
    """
    history: list[dict] = []
    board_tracker = BoardCursorTracker()

    for i, call in enumerate(script):
        name = call["name"]
        arguments = dict(call.get("arguments", {}))
        board_tracker.inject(name, arguments)
        result_text = await call_tool(name, arguments)
        board_tracker.extract(result_text)

        if game_log:
            game_log.emit("tool_call", tool=name, arguments=arguments, result=result_text)

        # Build initial user message from first pass_priority result
        if i == 0 and name == "pass_priority":
            try:
                result_data = json.loads(result_text)
                initial_message = build_initial_message(result_data)
            except (json.JSONDecodeError, TypeError):
                initial_message = "The game is starting. Call pass_priority to get your first decision."
            history.append({"role": "user", "content": initial_message})

        # Add assistant tool call + tool result to history
        tool_call_id = f"call_{i + 1}"
        history.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(arguments),
                        },
                    }
                ],
            }
        )
        history.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result_text,
            }
        )

        # Check for game over
        try:
            result_data = json.loads(result_text)
            if result_data.get("game_over") or result_data.get("player_dead"):
                break
        except (json.JSONDecodeError, TypeError):
            pass

    history_result = await call_tool("get_game_history", {})
    if game_log:
        game_log.emit("tool_call", tool="get_game_history", arguments={}, result=history_result)
    history_call_id = f"call_{len(script) + 1}"
    history.append(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": history_call_id,
                    "type": "function",
                    "function": {
                        "name": "get_game_history",
                        "arguments": json.dumps({}),
                    },
                }
            ],
        }
    )
    history.append(
        {
            "role": "tool",
            "tool_call_id": history_call_id,
            "content": history_result,
        }
    )

    return _render_context(history, system_prompt, state_summary="")


async def run_replay(
    server: str,
    port: int,
    username: str,
    project_root: Path,
    deck_path: Path | None = None,
    script: list[dict] | None = None,
    game_dir: Path | None = None,
    table_id: str | None = None,
) -> list[dict]:
    """Run the replay pilot.

    Executes scripted MCP tool calls, captures the final prompt, concedes.

    Returns the captured prompt messages array (what the LLM would see).
    """
    logger.info("[replay] Starting for %s@%s:%s", username, server, port)
    if script:
        logger.info("[replay] Script has %d calls", len(script))

    system_prompt = _load_default_system_prompt()

    # Build JVM args for the bridge (same as sleepwalker.py)
    jvm_args_list = [
        "--add-opens=java.base/java.io=ALL-UNNAMED",
        f"-Dxmage.bridge.server={server}",
        f"-Dxmage.bridge.port={port}",
        "-Dxmage.bridge.personality=sleepwalker",
    ]
    if table_id is not None:
        jvm_args_list.append(f"-Dxmage.bridge.tableId={table_id}")
    if sys.platform == "darwin":
        jvm_args_list.append("-Dapple.awt.UIElement=true")
    jvm_args = " ".join(jvm_args_list)

    mvn_args = ["-q", f"-Dxmage.bridge.username={username}"]
    if deck_path:
        mvn_args.append(f"-Dxmage.bridge.deck={deck_path}")
    if game_dir:
        mvn_args.append(f"-Dxmage.bridge.errorlog={game_dir / f'{username}_errors.log'}")
    mvn_args.append("exec:java")

    logger.info("[replay] Spawning bridge client...")

    game_log = None
    with ExitStack() as log_stack:
        if game_dir:
            game_log = log_stack.enter_context(GameLogWriter(game_dir, username))

        from puppeteer.bridge_transport import spawn_bridge_http

        async with spawn_bridge_http(
            mvn_args=mvn_args,
            project_root=project_root,
            jvm_args=jvm_args,
            log_file=game_dir / f"{username}_mcp.log" if game_dir else None,
        ) as session:
            result = await session.initialize()
            logger.debug("[replay] MCP initialized: %s", result.serverInfo)

            tools_result = await session.list_tools()
            tool_names = [t.name for t in tools_result.tools]
            logger.debug("[replay] Available tools: %s", tool_names)

            if game_log:
                game_log.emit("game_start", available_tools=tool_names)

            # Execute script via shared helper.
            async def call_tool(name: str, arguments: dict) -> str:
                return await execute_tool(session, name, arguments)

            script = script or []
            prompt = await execute_replay_script(call_tool, script, system_prompt, game_log)

            # Write prompt to file
            if game_dir:
                prompt_path = game_dir / f"{username}_golden_prompt.json"
                prompt_path.write_text(json.dumps(prompt, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
                logger.info("[replay] Prompt written to %s", prompt_path)

            # --- Concede to end the game ---
            logger.info("[replay] Conceding game...")
            concede_result = await execute_tool(session, "concede", {})
            logger.debug("[replay] Concede result: %s", concede_result)

            if game_log:
                game_log.emit("game_end", reason="replay_script_complete")

            logger.info("[replay] Done")
            return prompt


def main() -> int:
    """Main entry point for CLI usage."""
    setup_logging()
    parser = argparse.ArgumentParser(description="Replay pilot for XMage golden tests")
    parser.add_argument("--server", default="localhost", help="XMage server address")
    parser.add_argument("--port", type=int, default=17171, help="XMage server port")
    parser.add_argument("--username", default="Replay", help="Player username")
    parser.add_argument("--project-root", type=Path, help="Project root directory")
    parser.add_argument("--deck", type=Path, help="Path to deck file (.dck)")
    parser.add_argument("--script", type=Path, help="Path to script JSON file")
    parser.add_argument("--game-dir", type=Path, help="Game log directory")
    parser.add_argument("--table-id", help="UUID of the specific table to join")
    args = parser.parse_args()

    # Determine project root
    if args.project_root:
        project_root = args.project_root.resolve()
    else:
        project_root = Path.cwd().resolve()
        if project_root.name == "puppeteer" and project_root.parent.name == "src":
            project_root = project_root.parent.parent.parent
        elif project_root.name == "puppeteer":
            project_root = project_root.parent

    # Load script
    script: list[dict] = []
    if args.script:
        script = json.loads(args.script.read_text())

    try:
        asyncio.run(
            run_replay(
                server=args.server,
                port=args.port,
                username=args.username,
                project_root=project_root,
                deck_path=args.deck,
                script=script,
                game_dir=args.game_dir,
                table_id=args.table_id,
            )
        )
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
