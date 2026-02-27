"""Sleepwalker: MCP-based XMage player that plays automatically and sends occasional chat messages."""

import argparse
import asyncio
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

SLEEPY_NOISES = [
    "zzz",
    "zzzz",
    "zzzzz",
    "zzzzzz",
    "*snore*",
    "*mumble*",
    "...huh?",
    "*yawn*",
    "five more minutes...",
    "mmmph",
    "*drool*",
]


def get_sleepy_noise():
    """Return a random sleepy noise for chat messages."""
    return random.choice(SLEEPY_NOISES)


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


ACTION_DELAY_SECS = 0.5
CHAT_INTERVAL_SECS = 30


async def run_sleepwalker(
    server: str,
    port: int,
    username: str,
    project_root: Path,
    deck_path: Path | None = None,
) -> None:
    """Run the sleepwalker client."""
    _log(f"[sleepwalker] Starting for {username}@{server}:{port}")

    # Build JVM args for the bridge
    jvm_args_list = [
        "--add-opens=java.base/java.io=ALL-UNNAMED",
        f"-Dxmage.bridge.server={server}",
        f"-Dxmage.bridge.port={port}",
        "-Dxmage.bridge.personality=sleepwalker",
    ]
    if sys.platform == "darwin":
        jvm_args_list.append("-Dapple.awt.UIElement=true")
    jvm_args = " ".join(jvm_args_list)

    # Pass values that may contain spaces as Maven CLI args (not in MAVEN_OPTS)
    # because MAVEN_OPTS gets shell-split by the mvn script.
    mvn_args = ["-q", f"-Dxmage.bridge.username={username}"]
    if deck_path:
        mvn_args.append(f"-Dxmage.bridge.deck={deck_path}")
    mvn_args.append("exec:java")

    _log("[sleepwalker] Spawning bridge client...")

    from puppeteer.bridge_transport import spawn_bridge_http

    async with spawn_bridge_http(
        mvn_args=mvn_args,
        project_root=project_root,
        jvm_args=jvm_args,
    ) as session:
        # Initialize MCP connection
        result = await session.initialize()
        _log(f"[sleepwalker] MCP initialized: {result.serverInfo}")

        # List available tools
        tools = await session.list_tools()
        _log(f"[sleepwalker] Available tools: {[t.name for t in tools.tools]}")

        last_chat_time = time.time()
        last_log_length = 0

        _log("[sleepwalker] Entering main loop...")

        while True:
            try:
                # Wait for pending action (blocks until decision needed)
                result = await session.call_tool("pass_priority", {"timeout_ms": 15000})
                status = json.loads(result.content[0].text)

                if status.get("action_pending"):
                    action_type = status.get("action_type", "UNKNOWN")
                    _log(f"[sleepwalker] Action required: {action_type}")

                    # Delay before taking action
                    await asyncio.sleep(ACTION_DELAY_SECS)

                    # Pass priority (auto-handles the pending action)
                    await session.call_tool("pass_priority", {})
                    _log("[sleepwalker]   Result: passed")

                    # Print game log (only new entries since last check)
                    log_result = await session.call_tool("get_game_log", {"max_chars": 10000})
                    log_data = json.loads(log_result.content[0].text)
                    current_log = log_data.get("log", "")
                    total_length = log_data.get("total_length", 0)

                    # Print new log entries
                    if total_length > last_log_length:
                        # Get the new portion of the log
                        new_chars = total_length - last_log_length
                        if new_chars > 0 and len(current_log) >= new_chars:
                            new_log = current_log[-new_chars:]
                            if new_log.strip():
                                _log("[sleepwalker] === New Log Entries ===")
                                print(new_log)
                                _log("[sleepwalker] ========================")
                        last_log_length = total_length

                # Send periodic chat message
                current_time = time.time()
                if current_time - last_chat_time > CHAT_INTERVAL_SECS:
                    chat_message = get_sleepy_noise()
                    result = await session.call_tool("send_chat_message", {"message": chat_message})
                    chat_result = json.loads(result.content[0].text)
                    if chat_result.get("success"):
                        _log(f"[sleepwalker] Chat sent: {chat_message}")
                    else:
                        _log("[sleepwalker] Chat failed (no game active yet?)")
                    last_chat_time = current_time

                await asyncio.sleep(0.1)  # 100ms poll interval

            except KeyboardInterrupt:
                _log("[sleepwalker] Interrupted, shutting down...")
                break
            except Exception as e:
                _log(f"[sleepwalker] Error: {e}")
                await asyncio.sleep(1)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Sleepwalker MCP client for XMage")
    parser.add_argument("--server", default="localhost", help="XMage server address")
    parser.add_argument("--port", type=int, default=17171, help="XMage server port")
    parser.add_argument("--username", default="Sleepy", help="Player username")
    parser.add_argument("--project-root", type=Path, help="Project root directory")
    parser.add_argument("--deck", type=Path, help="Path to deck file (.dck)")
    args = parser.parse_args()

    # Determine project root
    if args.project_root:
        project_root = args.project_root.resolve()
    else:
        # Default: assume we're in the puppeteer directory
        project_root = Path.cwd().resolve()
        # If we're in puppeteer/src/puppeteer, go up
        if project_root.name == "puppeteer" and project_root.parent.name == "src":
            project_root = project_root.parent.parent.parent
        elif project_root.name == "puppeteer":
            project_root = project_root.parent

    _log(f"[sleepwalker] Project root: {project_root}")

    try:
        asyncio.run(
            run_sleepwalker(
                server=args.server,
                port=args.port,
                username=args.username,
                project_root=project_root,
                deck_path=args.deck,
            )
        )
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
