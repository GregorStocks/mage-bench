#!/usr/bin/env python3
"""Normalize generated MCP tool definitions into checked-in JSON5.

This script intentionally avoids non-stdlib dependencies so Java/bridge
validation can invoke it with plain ``python3``.
"""

import json
import re
import sys
from typing import Any


def format_mcp_tools_json5(raw_json: str) -> str:
    """Parse JSON emitted by McpServer and re-serialize as stable JSON5."""
    parsed: Any = json.loads(raw_json)
    text = json.dumps(parsed, indent=2, ensure_ascii=False)
    text = _add_trailing_commas(text)
    text = _expand_multiline_strings(text)
    return text + "\n"


def _add_trailing_commas(text: str) -> str:
    """Add trailing commas after the last value before } or ]."""
    return re.sub(r"([^\s,\[\{])\n(\s*[\]\}])", r"\1,\n\2", text)


def _expand_multiline_strings(text: str) -> str:
    r"""Expand \n escapes inside JSON strings into JSON5 line continuations."""
    result: list[str] = []
    i = 0
    in_string = False
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == "\\" and i + 1 < len(text):
                next_ch = text[i + 1]
                if next_ch == "n":
                    result.append("\\n\\\n")
                    i += 2
                    continue
                result.append(ch)
                result.append(next_ch)
                i += 2
                continue
            if ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        result.append(ch)
        i += 1
    return "".join(result)


def main() -> None:
    sys.stdout.write(format_mcp_tools_json5(sys.stdin.read()))


if __name__ == "__main__":
    main()
