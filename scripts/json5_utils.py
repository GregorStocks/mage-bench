"""JSON5 serialization utilities with multi-line string support.

Provides dumps_json5() for writing JSON5 with line continuations for multi-line
strings (dramatically better diffs), and loads_json5() for reading JSON5.
"""

import json
import re
from typing import Any

import json5


def loads_json5(text: str) -> Any:
    """Parse a JSON5 string. Also accepts standard JSON."""
    return json5.loads(text)


def dumps_json5(
    obj: object,
    *,
    indent: int = 2,
    sort_keys: bool = False,
    ensure_ascii: bool = False,
) -> str:
    """Serialize to JSON5 with multi-line strings and trailing commas.

    Strings containing newlines are split at \\n boundaries using JSON5 line
    continuations so each logical line appears on its own file line.
    """
    text = json.dumps(
        obj, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii
    )
    text = _add_trailing_commas(text)
    text = _expand_multiline_strings(text)
    return text


def _add_trailing_commas(text: str) -> str:
    """Add trailing comma after last element before } or ]."""
    return re.sub(r"([^\s,\[\{])\n(\s*[\]\}])", r"\1,\n\2", text)


def _expand_multiline_strings(text: str) -> str:
    r"""Expand \n escapes inside JSON strings into line continuations.

    Walks the text tracking string context so only \n inside strings (not \\n
    which is a literal backslash + n) gets expanded.
    """
    result: list[str] = []
    i = 0
    in_string = False
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == "\\":
                if i + 1 < len(text):
                    next_ch = text[i + 1]
                    if next_ch == "n":
                        # \n escape → \n + line continuation
                        result.append("\\n\\\n")
                        i += 2
                        continue
                    # Other escape sequence (\", \\, \t, etc.) — copy both chars
                    result.append(ch)
                    result.append(next_ch)
                    i += 2
                    continue
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        result.append(ch)
        i += 1
    return "".join(result)
