"""HTTP transport for spawning and connecting to bridge MCP servers.

Replaces the stdio-based transport with HTTP+SSE via the MCP SDK's
streamable HTTP client.  The bridge JVM starts an HTTP server on a
dynamically allocated port; the Python side waits for the port to
become reachable, then connects with ``streamable_http_client``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from puppeteer.port import PortReservation, find_available_port, wait_for_port
from puppeteer.process_manager import jvm_oom_preexec_fn, kill_tree

# MCP HTTP ports start at 19000 to avoid overlap with XMage server ports (17171+)
_MCP_PORT_START = 19000

# Timeout waiting for the bridge JVM to start its HTTP server
_BRIDGE_STARTUP_TIMEOUT_SECS = 120


@dataclass(frozen=True)
class BridgeLaunchArgs:
    jvm_args: str
    mvn_args: list[str]


def build_bridge_launch_args(
    *,
    server: str,
    port: int,
    username: str,
    deck_path: Path | None = None,
    heap_size_mb: int | None = None,
    table_id: str | None = None,
    error_log_path: Path | None = None,
    bridge_log_path: Path | None = None,
    max_interactions_per_turn: int | None = None,
) -> BridgeLaunchArgs:
    """Build the bridge JVM and Maven arguments for Python bridge clients."""
    jvm_args_list = [
        "--add-opens=java.base/java.io=ALL-UNNAMED",
    ]
    if heap_size_mb is not None:
        jvm_args_list.append(f"-Xmx{heap_size_mb}m")
    jvm_args_list.extend(
        [
            f"-Dxmage.bridge.server={server}",
            f"-Dxmage.bridge.port={port}",
        ]
    )
    if table_id is not None:
        jvm_args_list.append(f"-Dxmage.bridge.tableId={table_id}")
    if sys.platform == "darwin":
        jvm_args_list.append("-Dapple.awt.UIElement=true")

    mvn_args = ["-q", f"-Dxmage.bridge.username={username}"]
    if deck_path is not None:
        mvn_args.append(f"-Dxmage.bridge.deck={deck_path}")
    if error_log_path is not None:
        mvn_args.append(f"-Dxmage.bridge.errorlog={error_log_path}")
    if bridge_log_path is not None:
        mvn_args.append(f"-Dxmage.bridge.bridgelog={bridge_log_path}")
    if max_interactions_per_turn is not None:
        mvn_args.append(f"-Dxmage.bridge.maxInteractionsPerTurn={max_interactions_per_turn}")
    mvn_args.append("exec:java")

    return BridgeLaunchArgs(jvm_args=" ".join(jvm_args_list), mvn_args=mvn_args)


@asynccontextmanager
async def spawn_bridge_http(
    *,
    mvn_args: list[str],
    project_root: Path,
    jvm_args: str,
    env_updates: dict[str, str] | None = None,
    log_file: Path | None = None,
) -> AsyncGenerator[ClientSession, None]:
    """Spawn a bridge JVM with an MCP HTTP server and yield a ClientSession.

    Allocates a port, starts the bridge, waits for the HTTP server to be
    ready, and connects via the MCP SDK's streamable HTTP transport.

    The ``jvm_args`` string is augmented with ``-Dxmage.bridge.mcpPort=<port>``
    and placed in the ``MAVEN_OPTS`` environment variable.

    Args:
        mvn_args: Arguments for ``mvn`` (e.g. ``["-q", "-Dxmage.bridge.username=Bot", "exec:java"]``).
        project_root: Repository root (the bridge module is at ``project_root / "Mage.Client.Bridge"``).
        jvm_args: JVM options string (will be appended with the MCP port property).
        env_updates: Additional environment variables to merge.
        log_file: Optional path for stdout/stderr logging.

    Yields:
        A ``ClientSession`` connected to the bridge's MCP HTTP server.
    """
    port_reservation: PortReservation = find_available_port(_MCP_PORT_START)
    mcp_port = port_reservation.port

    full_jvm_args = f"{jvm_args} -Dxmage.bridge.mcpPort={mcp_port}"

    env = os.environ.copy()
    env["MAVEN_OPTS"] = full_jvm_args
    if env_updates:
        env.update(env_updates)

    log_fh = open(log_file, "w") if log_file else None
    proc: subprocess.Popen | None = None

    try:
        proc = subprocess.Popen(
            ["mvn", *mvn_args],
            cwd=str(project_root / "Mage.Client.Bridge"),
            stdin=subprocess.PIPE,
            stdout=log_fh or subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            env=env,
            preexec_fn=jvm_oom_preexec_fn(),
        )

        # Wait for the HTTP server to start listening.
        # Use 127.0.0.1 (not "localhost") to avoid DNS resolution that might
        # try IPv6 (::1) on CI, while the JDK HttpServer only binds IPv4.
        if not wait_for_port("127.0.0.1", mcp_port, _BRIDGE_STARTUP_TIMEOUT_SECS):
            rc = proc.poll()
            log_tail = ""
            if log_file and log_file.exists():
                log_tail = f"\nLog tail:\n{log_file.read_text()[-2000:]}"
            raise RuntimeError(
                f"Bridge MCP HTTP server did not start on port {mcp_port} "
                f"within {_BRIDGE_STARTUP_TIMEOUT_SECS}s (rc={rc}){log_tail}"
            )

        port_reservation.release()

        url = f"http://127.0.0.1:{mcp_port}/mcp"
        # pass_priority blocks until the opponent finishes their turn, which
        # can exceed 10 minutes for slow reasoning models.  The MCP SDK's
        # default SSE read timeout is 300s — too short.  Use an unlimited
        # read timeout so the connection stays alive for the whole game.
        http_client = create_mcp_http_client(
            timeout=httpx.Timeout(30.0, read=None),
        )
        async with (
            http_client,
            streamable_http_client(url, http_client=http_client) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            yield session

    finally:
        if proc is not None:
            # Close stdin to signal the bridge to shut down
            if proc.stdin:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                kill_tree(proc.pid)
        if log_fh:
            log_fh.close()
        port_reservation.release()
