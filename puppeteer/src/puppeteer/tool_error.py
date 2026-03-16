"""Shared exceptions and helpers for MCP tool execution."""

from mcp.types import CallToolResult, TextContent


class ToolExecutionError(RuntimeError):
    """Raised when an MCP tool call fails or returns malformed content."""


def extract_text_content(tool_name: str, result: CallToolResult) -> str:
    """Return the first text payload from an MCP tool result."""
    if not result.content or not isinstance(result.content[0], TextContent):
        raise ToolExecutionError(f"MCP tool {tool_name} returned no text content")
    return result.content[0].text
