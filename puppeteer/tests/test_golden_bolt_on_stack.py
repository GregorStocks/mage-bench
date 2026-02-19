"""Golden prompt test: Lightning Bolt on the stack."""

import pytest

from tests.golden_helpers import (
    DECK_BOLT_AND_BURN,
    DECK_FILLER,
    assert_golden_prompt,
    run_golden_scenario,
)


@pytest.mark.golden
def test_bolt_on_stack(xmage_server, tmp_path, project_root):
    """Lightning Bolt on the stack targeting the opponent.

    Script: choose starting player, keep hand, play Taiga, cast Lightning
    Bolt targeting Opponent, then get_game_state with Bolt still on stack.
    """
    server, port = xmage_server
    prompt = run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "bolt_on_stack",
        deck_a=DECK_BOLT_AND_BURN,
        deck_b=DECK_FILLER,
        script=[
            # Choose TestPlayer as starting player, keep hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # Play Taiga (alphabetical: Badlands=p3, Mountain=p4, Plateau=p5, Scrubland=p6, Taiga=p7).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p7"}},
            # Cast Lightning Bolt (alphabetical: Lightning Bolt=p8 index 0, Shock=p9 index 1).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            # Target Opponent.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p2"}},
            {"name": "get_game_state", "arguments": {}},
        ],
    )
    assert_golden_prompt("bolt_on_stack", prompt)
