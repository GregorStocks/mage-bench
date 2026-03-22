#!/usr/bin/env python3
"""Normalize generated MCP tool definitions into checked-in JSON5."""

import json
import sys
from typing import Any

from magebench.common.json5_writer import dumps_json5


def format_mcp_tools_json5(raw_json: str) -> str:
    """Parse JSON emitted by McpServer and re-serialize as stable JSON5."""
    parsed: Any = json.loads(raw_json)
    return dumps_json5(parsed) + "\n"


def main() -> None:
    sys.stdout.write(format_mcp_tools_json5(sys.stdin.read()))


if __name__ == "__main__":
    main()
