"""JSON5 parsing utilities plus the shared JSON5 serializer."""

from typing import Any

from scripts.json5_writer import dumps_json5

__all__ = ["dumps_json5", "loads_json5"]


def loads_json5(text: str | bytes) -> Any:
    """Parse a JSON5 string. Also accepts standard JSON."""
    import pyjson5

    if isinstance(text, bytes):
        text = text.decode()
    return pyjson5.loads(text)
