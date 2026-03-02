"""Golden prompt test: initial decision point."""

import pytest

from tests.golden_helpers import (
    DECK_GOBLINS,
    DECK_RED_STOMPY,
    run_golden_scenario,
)


@pytest.mark.golden
def test_initial_decision(xmage_server, tmp_path, project_root, bridge_session, opponent_session, spectator_process):
    """Verify the prompt at the very first LLM decision point.

    Script: pass_priority (to get the initial decision) then get_game_state.
    The golden file captures the full messages array the LLM would receive
    at this point — system prompt, initial user message, and two tool results.
    """
    server, port = xmage_server
    run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "initial_decision",
        deck_a=DECK_RED_STOMPY,
        deck_b=DECK_GOBLINS,
        script_a=[
            # Choose TestPlayer as starting player, keep hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # First real decision point — capture state.
            {"name": "pass_priority", "arguments": {}},
            {"name": "get_game_state", "arguments": {}},
        ],
        golden_name="initial_decision",
        bridge_a=bridge_session,
        bridge_b=opponent_session,
        spectator=spectator_process,
    )
