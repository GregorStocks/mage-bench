"""Tests for scripts.mcp_tools_json5."""

from scripts.json5_utils import loads_json5
from scripts.mcp_tools_json5 import format_mcp_tools_json5


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
