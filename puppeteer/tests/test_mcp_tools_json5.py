"""Tests for scripts.mcp_tools_json5."""

import os
import shutil
import subprocess
from pathlib import Path

from magebench.common.json5_utils import loads_json5
from scripts.mcp_tools_json5 import format_mcp_tools_json5

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_format_mcp_tools_json5_preserves_structure_and_uses_json5() -> None:
    raw_json = (
        '[{"name":"choose_action","description":"Line 1\\nLine 2",'
        '"inputSchema":{"type":"object","properties":{"index":{"type":"integer"}}}}]'
    )

    formatted = format_mcp_tools_json5(raw_json)

    assert formatted.endswith("\n")
    assert ",\n" in formatted
    assert "\\n\\\n" in formatted
    assert loads_json5(formatted) == [
        {
            "name": "choose_action",
            "description": "Line 1\nLine 2",
            "inputSchema": {
                "type": "object",
                "properties": {"index": {"type": "integer"}},
            },
        }
    ]


def test_cli_runs_under_system_python_without_site_packages() -> None:
    python3 = shutil.which("python3")
    assert python3 is not None
    pythonpath = os.pathsep.join(["../src", ".."])

    result = subprocess.run(
        [python3, "-S", "-m", "scripts.mcp_tools_json5"],
        cwd=REPO_ROOT / "Mage.Client.Bridge",
        env={**os.environ, "PYTHONPATH": pythonpath},
        input='[{"name":"choose_action"}]',
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.endswith("\n")
    assert '"name": "choose_action"' in result.stdout
