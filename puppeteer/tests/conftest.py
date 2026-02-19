"""Shared fixtures for golden prompt integration tests."""

import os
import subprocess
from pathlib import Path

import pytest

from puppeteer.orchestrator import compile_project
from puppeteer.port import find_available_port, wait_for_port
from puppeteer.process_manager import kill_tree
from puppeteer.xml_config import modify_server_config


@pytest.fixture(scope="session")
def project_root():
    """Project root directory."""
    return Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="session")
def xmage_server(project_root, tmp_path_factory):
    """Compile project and start XMage server for golden integration tests.

    Yields (server_host, port).

    Requires GOLDEN_INTEGRATION=1 environment variable. Skips otherwise,
    so `make test` (which runs all tests) doesn't trigger a slow server
    startup. Use `make test-golden` to run these tests explicitly.
    """
    if not os.environ.get("GOLDEN_INTEGRATION"):
        pytest.skip("Golden integration tests: run with 'make test-golden'")

    # Compile all needed modules
    assert compile_project(project_root, streaming=True), "Compilation failed"

    # Find available port
    port_res = find_available_port("localhost", 17171)
    port = port_res.port

    # Generate server config
    tmp_dir = tmp_path_factory.mktemp("xmage_server")
    config_path = tmp_dir / "server_config.xml"
    modify_server_config(
        source=project_root / "Mage.Server" / "config" / "config.xml",
        destination=config_path,
        port=port,
    )

    # Build JVM options (server is headless; clients need AWT for Swing)
    jvm_opts = " ".join(
        [
            "--add-opens=java.base/java.io=ALL-UNNAMED",
            "-Djava.awt.headless=true",
        ]
    )

    # Start server
    env = os.environ.copy()
    env.update(
        {
            "XMAGE_AI_PUPPETEER": "1",
            "XMAGE_AI_PUPPETEER_USER": "spectator",
            "XMAGE_AI_PUPPETEER_PASSWORD": "",
            "XMAGE_AI_PUPPETEER_SERVER": "localhost",
            "XMAGE_AI_PUPPETEER_PORT": str(port),
            "XMAGE_AI_PUPPETEER_DISABLE_WHATS_NEW": "1",
            "MAVEN_OPTS": f"{jvm_opts} -Dxmage.config.path={config_path}",
        }
    )

    server_log = tmp_dir / "server.log"
    server_log_fh = open(server_log, "w")
    server_proc = subprocess.Popen(
        ["mvn", "-q", "exec:java"],
        cwd=project_root / "Mage.Server",
        env=env,
        stdout=server_log_fh,
        stderr=subprocess.STDOUT,
    )

    try:
        assert wait_for_port("localhost", port, 90), f"XMage server failed to start within 90s — check {server_log}"
        port_res.release()
        yield "localhost", port
    finally:
        kill_tree(server_proc.pid)
        server_log_fh.close()
