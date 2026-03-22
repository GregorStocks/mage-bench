"""Tests for the blunder experiment approaches."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from magebench.analysis.toolbox.blunder_experiment import OPUS, _call_llm, _parse_inline_response


def _ann(**overrides: object) -> dict:
    """Create a minimal valid annotation dict."""
    json_key_by_field = {
        "decision_index": "decisionIndex",
        "action_taken": "actionTaken",
        "better_line": "betterLine",
        "snapshot_index": "snapshotIndex",
        "llm_reasoning": "llmReasoning",
    }
    base: dict = {
        "decisionIndex": 0,
        "player": "A",
        "type": "blunder",
        "severity": "minor",
        "category": "x",
        "description": "d",
        "actionTaken": "a",
        "betterLine": "b",
    }
    base.update({json_key_by_field.get(field_name, field_name): value for field_name, value in overrides.items()})
    return base


class TestParseInlineResponse:
    def test_pass_only(self) -> None:
        text = "PASS\nPASS\nPASS"
        assert _parse_inline_response(text) == []

    def test_single_annotation(self) -> None:
        a = _ann(decision_index=5, category="unused_mana", description="test")
        text = f"PASS\n{json.dumps(a)}\nPASS"
        result = _parse_inline_response(text)
        assert len(result) == 1
        assert result[0]["decisionIndex"] == 5
        assert result[0]["severity"] == "minor"

    def test_multiple_annotations(self) -> None:
        a1 = _ann(decision_index=3, severity="major")
        a2 = _ann(decision_index=7, player="B", category="y")
        text = f"PASS\n{json.dumps(a1)}\nPASS\nPASS\n{json.dumps(a2)}\n"
        result = _parse_inline_response(text)
        assert len(result) == 2
        assert result[0]["decisionIndex"] == 3
        assert result[1]["decisionIndex"] == 7

    def test_ignores_non_annotation_json(self) -> None:
        a = _ann(decision_index=1)
        text = f'{{"foo": "bar"}}\nPASS\n{json.dumps(a)}'
        result = _parse_inline_response(text)
        assert len(result) == 1
        assert result[0]["decisionIndex"] == 1

    def test_handles_markdown_wrapped_json(self) -> None:
        a = _ann(decision_index=2, severity="moderate")
        text = f"PASS\n\n```json\n{json.dumps(a)}\n```\n\nPASS"
        result = _parse_inline_response(text)
        assert len(result) == 1
        assert result[0]["decisionIndex"] == 2

    def test_empty_response(self) -> None:
        assert _parse_inline_response("") == []

    def test_nested_json_in_strings(self) -> None:
        a = _ann(decision_index=4, description="cast {2}{R}{R} spell")
        text = f"PASS\n{json.dumps(a)}\nPASS"
        result = _parse_inline_response(text)
        assert len(result) == 1
        assert result[0]["decisionIndex"] == 4
        assert "{2}{R}{R}" in result[0]["description"]


def _fake_completion_response() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="[]"))],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            completion_tokens_details=None,
        ),
    )


def test_call_llm_uses_temperature_without_reasoning_effort() -> None:
    create = MagicMock(return_value=_fake_completion_response())
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    _call_llm(client, OPUS, "system", "user", label="baseline")

    kwargs = create.call_args.kwargs
    assert kwargs["temperature"] == 0
    assert "extra_body" not in kwargs


def test_call_llm_uses_reasoning_effort_when_requested() -> None:
    create = MagicMock(return_value=_fake_completion_response())
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    _call_llm(client, OPUS, "system", "user", reasoning_effort="medium", label="reasoned")

    kwargs = create.call_args.kwargs
    assert kwargs["extra_body"] == {"reasoning": {"effort": "medium"}}
    assert "temperature" not in kwargs
