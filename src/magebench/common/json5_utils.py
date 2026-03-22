"""JSON5 parsing utilities."""

import builtins
from typing import Any


def _load_pyjson5() -> Any:
    """Resolve pyjson5 lazily so JSON5 parsing remains optional until needed."""
    return builtins.__import__("pyjson5")


def loads_json5(text: str | bytes) -> Any:
    """Parse a JSON5 string. Also accepts standard JSON."""
    if isinstance(text, bytes):
        text = text.decode()
    return _load_pyjson5().loads(text)
