"""Tests for the blunder experiment approaches."""

import json

from blunder_experiment import _parse_inline_response


def _ann(**overrides: object) -> dict:
    """Create a minimal valid annotation dict."""
    base: dict = {
        "snapshotIndex": 0,
        "player": "A",
        "type": "blunder",
        "severity": "minor",
        "category": "x",
        "description": "d",
        "llmReasoning": "r",
        "actionTaken": "a",
        "betterLine": "b",
    }
    base.update(overrides)
    return base


class TestParseInlineResponse:
    def test_pass_only(self) -> None:
        text = "PASS\nPASS\nPASS"
        assert _parse_inline_response(text) == []

    def test_single_annotation(self) -> None:
        a = _ann(snapshotIndex=5, category="unused_mana", description="test")
        text = f"PASS\n{json.dumps(a)}\nPASS"
        result = _parse_inline_response(text)
        assert len(result) == 1
        assert result[0]["snapshotIndex"] == 5
        assert result[0]["severity"] == "minor"

    def test_multiple_annotations(self) -> None:
        a1 = _ann(snapshotIndex=3, severity="major")
        a2 = _ann(snapshotIndex=7, player="B", category="y")
        text = f"PASS\n{json.dumps(a1)}\nPASS\nPASS\n{json.dumps(a2)}\n"
        result = _parse_inline_response(text)
        assert len(result) == 2
        assert result[0]["snapshotIndex"] == 3
        assert result[1]["snapshotIndex"] == 7

    def test_ignores_non_annotation_json(self) -> None:
        a = _ann(snapshotIndex=1)
        text = f'{{"foo": "bar"}}\nPASS\n{json.dumps(a)}'
        result = _parse_inline_response(text)
        assert len(result) == 1
        assert result[0]["snapshotIndex"] == 1

    def test_handles_markdown_wrapped_json(self) -> None:
        a = _ann(snapshotIndex=2, severity="moderate")
        text = f"PASS\n\n```json\n{json.dumps(a)}\n```\n\nPASS"
        result = _parse_inline_response(text)
        assert len(result) == 1
        assert result[0]["snapshotIndex"] == 2

    def test_empty_response(self) -> None:
        assert _parse_inline_response("") == []

    def test_nested_json_in_strings(self) -> None:
        a = _ann(snapshotIndex=4, description="cast {2}{R}{R} spell")
        text = f"PASS\n{json.dumps(a)}\nPASS"
        result = _parse_inline_response(text)
        assert len(result) == 1
        assert result[0]["snapshotIndex"] == 4
        assert "{2}{R}{R}" in result[0]["description"]
