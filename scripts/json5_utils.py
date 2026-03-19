"""JSON5 read/write utilities.

Provides dumps_json5() for writing JSON5 with line continuations for multi-line
strings (dramatically better diffs), and loads_json5() for reading JSON5.
"""

from typing import Any

import pyjson5

from scripts.json5_dump import dumps_json5

__all__ = ["dumps_json5", "loads_json5"]


def loads_json5(text: str | bytes) -> Any:
    """Parse a JSON5 string. Also accepts standard JSON."""
    if isinstance(text, bytes):
        text = text.decode()
    return pyjson5.loads(text)
