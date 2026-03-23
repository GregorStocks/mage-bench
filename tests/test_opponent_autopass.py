"""Unit tests for the golden opponent autopass helper."""

from __future__ import annotations

import json

from tests.golden_helpers import _run_opponent_autopass


class _FakeBridge:
    def __init__(self, responses: list[tuple[str, dict]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        assert self._responses, f"Unexpected tool call: {name}"
        expected_name, response = self._responses.pop(0)
        assert name == expected_name, f"Expected {expected_name}, got {name}"
        return json.dumps(response)


def test_opponent_autopass_declines_playable_cards_prompt() -> None:
    bridge = _FakeBridge(
        [
            (
                "pass_priority",
                {"action_pending": True, "stop_reason": "playable_cards"},
            ),
            ("choose_action", {"success": True}),
            ("pass_priority", {"game_over": True}),
        ]
    )

    _run_opponent_autopass(bridge)

    assert bridge.calls == [
        ("pass_priority", {"until": "end_of_turn"}),
        ("choose_action", {"choice": "no"}),
        ("pass_priority", {"until": "end_of_turn"}),
    ]
