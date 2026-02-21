"""Golden prompt test: initial decision point."""

import pytest

from tests.golden_helpers import (
    DECK_GOBLINS,
    DECK_RED_STOMPY,
    assert_golden_export,
    assert_golden_prompt,
    run_golden_scenario,
)


@pytest.mark.golden
def test_initial_decision(xmage_server, tmp_path, project_root):
    """Verify the prompt at the very first LLM decision point.

    Script: pass_priority (to get the initial decision) then get_game_state.
    The golden file captures the full messages array the LLM would receive
    at this point — system prompt, initial user message, and two tool results.
    """
    server, port = xmage_server
    prompt = run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "initial_decision",
        deck_a=DECK_RED_STOMPY,
        deck_b=DECK_GOBLINS,
        script=[
            {"name": "pass_priority", "arguments": {}},
            {"name": "get_game_state", "arguments": {}},
        ],
    )
    assert_golden_prompt("initial_decision", prompt)
    assert_golden_export("initial_decision", tmp_path / "initial_decision")
