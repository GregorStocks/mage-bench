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

from puppeteer.bridge_transport import spawn_bridge_http
from puppeteer.config import load_prompts
from puppeteer.game_log import GameLogWriter
from puppeteer.log import get_logger, setup_logging
from puppeteer.pilot import BoardCursorTracker, _render_context, _render_for_pilot, build_initial_message, execute_tool

logger = get_logger(__name__)
_ASSERT_ACTION_STEP = "assert_action"
_ASSERT_ACTION_FIELDS = ("action_type", "response_type", "combat_phase", "stop_reason")


def _load_default_system_prompt() -> str:
    """Load the default system prompt from prompts.json."""
    prompts = load_prompts(None)
    assert "default" in prompts, "prompts.json must contain a 'default' key"
    return prompts["default"]


AsyncCallToolFn = Callable[[str, dict], Awaitable[str]]


def _is_meta_script_step(step: dict) -> bool:
    """Return True for replay-script steps that validate state instead of calling tools."""
    return step.get("name") == _ASSERT_ACTION_STEP


def _run_meta_script_step(step: dict, *, last_tool_name: str | None, last_result_text: str | None) -> None:
    """Validate the latest tool result against an assertion-only script step."""
    assert step.get("name") == _ASSERT_ACTION_STEP, f"Unknown meta script step: {step.get('name')!r}"
    if last_result_text is None:
        raise AssertionError("assert_action requires a preceding tool result")

    try:
        data = json.loads(last_result_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise AssertionError(
            f"assert_action after {last_tool_name or '?'} requires a JSON object result, got: {last_result_text!r}"
        ) from exc

    if not isinstance(data, dict):
        raise AssertionError(
            f"assert_action after {last_tool_name or '?'} requires a JSON object result, got: {type(data).__name__}"
        )
    if not data.get("action_pending"):
        raise AssertionError(
            f"assert_action after {last_tool_name or '?'} expected action_pending=true, got: {last_result_text}"
        )

    arguments = dict(step.get("arguments", {}))
    allowed = set(_ASSERT_ACTION_FIELDS) | {"message_contains"}
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise AssertionError(f"assert_action got unsupported arguments: {', '.join(unknown)}")

    for field in _ASSERT_ACTION_FIELDS:
        if field in arguments and data.get(field) != arguments[field]:
            raise AssertionError(
                f"assert_action after {last_tool_name or '?'} expected {field}={arguments[field]!r}, "
                f"got {data.get(field)!r}"
            )

    if "message_contains" in arguments:
        message = data.get("message")
        expected = arguments["message_contains"]
        if not isinstance(message, str) or expected not in message:
            raise AssertionError(
                f"assert_action after {last_tool_name or '?'} expected message containing {expected!r}, got {message!r}"
            )


async def execute_replay_script(
    call_tool: AsyncCallToolFn,
    script: list[dict],
    system_prompt: str,
    game_log: GameLogWriter | None = None,
    skip_postscript: bool = False,
) -> list[dict]:
    """Execute a replay script and return the captured prompt messages.

    This is the shared core of both ``run_replay`` (async MCP subprocess) and
    the persistent-session path in golden_helpers. The ``call_tool`` callable
    abstracts over the transport — async MCP SDK or sync JSON-RPC (wrapped).
    """
    history: list[dict] = []
    board_tracker = BoardCursorTracker()
    last_board: list[dict] | None = None
    last_result_text: str | None = None
    last_tool_name: str | None = None
    seen_oracle_cards: set[str] = set()
    tool_call_count = 0

    rendered_tools = frozenset({"pass_priority", "get_action_choices", "choose_action"})

    for call in script:
        if _is_meta_script_step(call):
            _run_meta_script_step(call, last_tool_name=last_tool_name, last_result_text=last_result_text)
            continue

        name = call["name"]
        arguments = dict(call.get("arguments", {}))
        tool_call_count += 1
        board_tracker.inject(name, arguments)
        result_text = await call_tool(name, arguments)
        board_tracker.extract(result_text)
        last_tool_name = name
        last_result_text = result_text

        if game_log:
            game_log.emit("tool_call", tool=name, arguments=arguments, result=result_text)

        # Build initial user message from first pass_priority result
        if tool_call_count == 1 and name == "pass_priority":
            try:
                result_data = json.loads(result_text)
                initial_message = build_initial_message(result_data)
            except (json.JSONDecodeError, TypeError):
                initial_message = "The game is starting. Call pass_priority to get your first decision."
            history.append({"role": "user", "content": initial_message})

        # Render action results the same way the real pilot does,
        # so golden prompts match what the LLM actually sees.
        display_text = result_text
        if name in rendered_tools:
            display_text, last_board = _render_for_pilot(result_text, last_board, seen_oracle_cards)

        # Add assistant tool call + tool result to history
        tool_call_id = f"call_{tool_call_count}"
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
                "content": display_text,
            }
        )

        # Check for game over
        try:
            result_data = json.loads(result_text)
            game_ended = (
                result_data.get("game_over")
                or result_data.get("player_dead")
                or result_data.get("stop_reason") == "game_over"
            )
            if game_ended:
                break
        except (json.JSONDecodeError, TypeError):
            pass

    # Capture final game state + history for prompt display.
    # skip_postscript=True skips these — used for player B to avoid racing
    # with player A's post-script calls after the game ends.
    if not skip_postscript:
        state_result = await call_tool("get_game_state", {})
        if game_log:
            game_log.emit("tool_call", tool="get_game_state", arguments={}, result=state_result)
        state_call_id = f"call_{tool_call_count + 1}"
        history.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": state_call_id,
                        "type": "function",
                        "function": {
                            "name": "get_game_state",
                            "arguments": json.dumps({}),
                        },
                    }
                ],
            }
        )
        history.append(
            {
                "role": "tool",
                "tool_call_id": state_call_id,
                "content": state_result,
            }
        )

        history_result = await call_tool("get_game_history", {})
        if game_log:
            game_log.emit("tool_call", tool="get_game_history", arguments={}, result=history_result)
        history_call_id = f"call_{tool_call_count + 2}"
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
    script: list[dict],
    deck_path: Path | None = None,
    game_dir: Path | None = None,
    table_id: str | None = None,
    skip_postscript: bool = False,
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

            prompt = await execute_replay_script(
                call_tool, script, system_prompt, game_log, skip_postscript=skip_postscript
            )

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
    parser.add_argument(
        "--skip-postscript",
        action="store_true",
        help="Skip post-script get_game_state and get_game_history calls",
    )
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
                skip_postscript=args.skip_postscript,
            )
        )
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
