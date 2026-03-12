"""Shared exceptions and helpers for MCP tool execution."""

from collections.abc import Sequence
from typing import Protocol


class ToolExecutionError(RuntimeError):
    """Raised when an MCP tool call fails or returns malformed content."""


class _ToolTextContent(Protocol):
    text: str


class _ToolResult(Protocol):
    content: Sequence[_ToolTextContent]


def extract_text_content(tool_name: str, result: _ToolResult) -> str:
    """Return the first text payload from an MCP tool result."""
    try:
        return result.content[0].text
    except (AttributeError, IndexError, TypeError) as exc:
        raise ToolExecutionError(f"MCP tool {tool_name} returned no text content") from exc
