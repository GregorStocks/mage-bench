"""Shared exceptions and helpers for MCP tool execution."""

from mcp.types import CallToolResult


class ToolExecutionError(RuntimeError):
    """Raised when an MCP tool call fails or returns malformed content."""


def extract_text_content(tool_name: str, result: CallToolResult) -> str:
    """Return the first text payload from an MCP tool result.

    Uses duck-typed .text access because the MCP HTTP bridge transport
    returns SimpleNamespace objects, not real TextContent instances.
    """
    if not result.content:
        raise ToolExecutionError(f"MCP tool {tool_name} returned no text content")
    text: str | None = getattr(result.content[0], "text", None)
    if text is None:
        raise ToolExecutionError(f"MCP tool {tool_name} returned no text content")
    return text
