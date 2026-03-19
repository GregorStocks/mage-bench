"""JSON5 serialization utilities with multi-line string support.

Provides dumps_json5() for writing JSON5 with line continuations for multi-line
strings (dramatically better diffs), and loads_json5() for reading JSON5.
"""

import json
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
    return _serialize(
        obj, indent=indent, level=0, sort_keys=sort_keys, ensure_ascii=ensure_ascii
    )


def _serialize(
    obj: object,
    *,
    indent: int,
    level: int,
    sort_keys: bool,
    ensure_ascii: bool,
) -> str:
    if obj is None:
        return "null"
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, int):
        return str(obj)
    if isinstance(obj, float):
        if obj != obj:  # NaN
            return "NaN"
        if obj == float("inf"):
            return "Infinity"
        if obj == float("-inf"):
            return "-Infinity"
        return repr(obj)
    if isinstance(obj, str):
        return _serialize_string(
            obj, indent=indent, level=level, ensure_ascii=ensure_ascii
        )
    if isinstance(obj, dict):
        return _serialize_dict(
            obj,
            indent=indent,
            level=level,
            sort_keys=sort_keys,
            ensure_ascii=ensure_ascii,
        )
    if isinstance(obj, (list, tuple)):
        return _serialize_list(
            obj,
            indent=indent,
            level=level,
            sort_keys=sort_keys,
            ensure_ascii=ensure_ascii,
        )
    raise TypeError(f"Cannot serialize {type(obj).__name__}")


def _serialize_string(s: str, *, indent: int, level: int, ensure_ascii: bool) -> str:
    """Serialize a string, using JSON5 line continuations for multi-line content."""
    if "\n" not in s:
        return json.dumps(s, ensure_ascii=ensure_ascii)

    # Split on newline boundaries. Each segment (except the last) gets \n appended.
    # Segments are joined with backslash-newline (line continuation).
    # Continuation lines must start at column 0 — any whitespace would become
    # part of the string value.
    lines = s.split("\n")
    segments = []
    for i, line in enumerate(lines):
        # Escape the line content using json.dumps, strip surrounding quotes
        escaped = json.dumps(line, ensure_ascii=ensure_ascii)[1:-1]
        if i < len(lines) - 1:
            segments.append(escaped + "\\n")
        else:
            segments.append(escaped)

    return '"' + "\\\n".join(segments) + '"'


def _serialize_dict(
    obj: dict,
    *,
    indent: int,
    level: int,
    sort_keys: bool,
    ensure_ascii: bool,
) -> str:
    if not obj:
        return "{}"

    inner_indent = " " * (indent * (level + 1))
    close_indent = " " * (indent * level)

    keys = sorted(obj.keys()) if sort_keys else list(obj.keys())
    parts = []
    for key in keys:
        key_str = json.dumps(str(key), ensure_ascii=ensure_ascii)
        value_str = _serialize(
            obj[key],
            indent=indent,
            level=level + 1,
            sort_keys=sort_keys,
            ensure_ascii=ensure_ascii,
        )
        parts.append(f"{inner_indent}{key_str}: {value_str},")

    return "{\n" + "\n".join(parts) + "\n" + close_indent + "}"


def _serialize_list(
    obj: list | tuple,
    *,
    indent: int,
    level: int,
    sort_keys: bool,
    ensure_ascii: bool,
) -> str:
    if not obj:
        return "[]"

    inner_indent = " " * (indent * (level + 1))
    close_indent = " " * (indent * level)

    parts = []
    for item in obj:
        value_str = _serialize(
            item,
            indent=indent,
            level=level + 1,
            sort_keys=sort_keys,
            ensure_ascii=ensure_ascii,
        )
        parts.append(f"{inner_indent}{value_str},")

    return "[\n" + "\n".join(parts) + "\n" + close_indent + "]"
