"""Tests for scripts.json5_utils — JSON5 serialization with multi-line strings."""

from scripts.json5_utils import dumps_json5, loads_json5


def test_round_trip_null() -> None:
    assert loads_json5(dumps_json5(None)) is None


def test_round_trip_bool() -> None:
    assert loads_json5(dumps_json5(True)) is True
    assert loads_json5(dumps_json5(False)) is False


def test_round_trip_int() -> None:
    assert loads_json5(dumps_json5(42)) == 42
    assert loads_json5(dumps_json5(0)) == 0
    assert loads_json5(dumps_json5(-7)) == -7


def test_round_trip_float() -> None:
    assert loads_json5(dumps_json5(3.14)) == 3.14


def test_round_trip_simple_string() -> None:
    assert loads_json5(dumps_json5("hello")) == "hello"
    assert loads_json5(dumps_json5("")) == ""


def test_round_trip_string_with_special_chars() -> None:
    s = 'tab\there "quotes" and \\backslash'
    assert loads_json5(dumps_json5(s)) == s


def test_round_trip_multiline_string() -> None:
    s = "line1\nline2\nline3"
    result = dumps_json5(s)
    assert loads_json5(result) == s


def test_multiline_string_format() -> None:
    """Multi-line strings use \\n\\ line continuations."""
    s = "line1\nline2\nline3"
    result = dumps_json5(s)
    # Should contain actual file newlines (line continuations)
    assert result.count("\n") >= 2
    # Each \\n should be followed by a backslash-newline continuation
    assert "\\n\\\n" in result


def test_continuation_lines_not_indented() -> None:
    """Continuation lines must start at column 0 to avoid injecting whitespace."""
    obj = {"key": "line1\nline2\nline3"}
    result = dumps_json5(obj)
    file_lines = result.split("\n")
    # Find the continuation lines (those that are part of a multi-line string,
    # after the first line which has the key + indent)
    for line in file_lines:
        # Continuation lines start with the string content, not whitespace
        # The first line of the string value starts with indent + "key": "...
        # Continuation lines (after \\\n) must start at column 0
        if line and not line.startswith((" ", "{", "}", "[", "]", '"')):
            # This is a continuation line — verify no leading whitespace
            assert line == line.lstrip(), f"Continuation line has leading whitespace: {line!r}"


def test_string_ending_with_newline() -> None:
    s = "hello\nworld\n"
    result = dumps_json5(s)
    assert loads_json5(result) == s


def test_string_only_newline() -> None:
    s = "\n"
    result = dumps_json5(s)
    assert loads_json5(result) == s


def test_string_multiple_newlines() -> None:
    s = "\n\n"
    result = dumps_json5(s)
    assert loads_json5(result) == s


def test_string_with_literal_backslash_n() -> None:
    """A string containing literal \\n (not a newline) should round-trip correctly."""
    s = "not\\na\\nnewline"
    result = dumps_json5(s)
    assert loads_json5(result) == s


def test_trailing_commas_in_dict() -> None:
    result = dumps_json5({"a": 1, "b": 2})
    # Each value line should end with a comma
    lines = result.strip().split("\n")
    for line in lines[1:-1]:  # skip opening { and closing }
        assert line.rstrip().endswith(","), f"Missing trailing comma: {line!r}"


def test_trailing_commas_in_list() -> None:
    result = dumps_json5([1, 2, 3])
    lines = result.strip().split("\n")
    for line in lines[1:-1]:  # skip opening [ and closing ]
        assert line.rstrip().endswith(","), f"Missing trailing comma: {line!r}"


def test_sort_keys() -> None:
    obj = {"c": 3, "a": 1, "b": 2}
    result = dumps_json5(obj, sort_keys=True)
    lines = result.strip().split("\n")
    # Extract keys from output (lines like '  "key": value,')
    keys = []
    for line in lines[1:-1]:
        key = line.strip().split(":")[0].strip().strip('"')
        keys.append(key)
    assert keys == ["a", "b", "c"]


def test_sort_keys_false_preserves_order() -> None:
    obj = {"c": 3, "a": 1, "b": 2}
    result = dumps_json5(obj, sort_keys=False)
    lines = result.strip().split("\n")
    keys = []
    for line in lines[1:-1]:
        key = line.strip().split(":")[0].strip().strip('"')
        keys.append(key)
    assert keys == ["c", "a", "b"]


def test_empty_dict() -> None:
    assert dumps_json5({}) == "{}"
    assert loads_json5(dumps_json5({})) == {}


def test_empty_list() -> None:
    assert dumps_json5([]) == "[]"
    assert loads_json5(dumps_json5([])) == []


def test_nested_structure() -> None:
    obj = {
        "name": "test",
        "items": [1, "two", None],
        "nested": {"a": True, "b": False},
    }
    result = dumps_json5(obj)
    assert loads_json5(result) == obj


def test_nested_multiline_strings() -> None:
    """Multi-line strings inside nested structures round-trip correctly."""
    obj = {
        "outer": {
            "system": "You are an expert.\n\nAnalyze carefully.\nBe thorough.",
            "user": "Context:\n- Item 1\n- Item 2",
        },
    }
    result = dumps_json5(obj)
    assert loads_json5(result) == obj


def test_blunder_golden_shaped_data() -> None:
    """Round-trip a realistic blunder prompt structure."""
    obj = {
        "decision_index": 7,
        "turn": 2,
        "phase": "POSTCOMBAT_MAIN",
        "player": "TestPlayer",
        "message": "Play instants and activated abilities",
        "system": "You are a Magic: The Gathering expert.\n\nAnalyze the decision.\nIf reasonable, return null.\n",
        "user": "## Game Overview\nGame: bolt_on_stack\n\n## Decision\nTurn 2\n",
    }
    result = dumps_json5(obj)
    parsed = loads_json5(result)
    assert parsed == obj


def test_unicode_preserved() -> None:
    s = "Jace, the Mind Sculptor \u2014 Planeswalker"
    result = dumps_json5(s, ensure_ascii=False)
    assert "\u2014" in result
    assert loads_json5(result) == s


def test_multiline_with_unicode() -> None:
    s = "Line with \u2014 dash\nNext line"
    result = dumps_json5(s, ensure_ascii=False)
    assert loads_json5(result) == s
