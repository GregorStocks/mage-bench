"""JSON5 parsing utilities plus the shared JSON5 serializer."""

import builtins
from typing import Any

from scripts.json5_writer import dumps_json5

__all__ = ["dumps_json5", "loads_json5"]


def _load_pyjson5() -> Any:
    """Resolve pyjson5 lazily so dumps_json5 remains usable without it."""
    return builtins.__import__("pyjson5")


def loads_json5(text: str | bytes) -> Any:
    """Parse a JSON5 string. Also accepts standard JSON."""
    if isinstance(text, bytes):
        text = text.decode()
    return _load_pyjson5().loads(text)
