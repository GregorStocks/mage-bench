"""Golden prompt test: Lightning Bolt with stack_resolved yield."""

import pytest

from tests.golden_helpers import (
    DECK_BOLT_AND_BURN,
    DECK_FILLER,
    run_golden_scenario,
)


@pytest.mark.golden
def test_stack_resolved(xmage_server, tmp_path, project_root, spectator_process):
    """Cast Lightning Bolt, then pass_priority(until="stack_resolved") to let it resolve.

    Script:
    - Choose TestPlayer as starting player, keep hand
    - T1: Play Mountain, cast Lightning Bolt targeting Opponent
    - pass_priority(until="stack_resolved") — bolt resolves
    - get_game_state to verify Opponent at 17 life
    """
    server, port = xmage_server
    run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "stack_resolved",
        deck_a=DECK_BOLT_AND_BURN,
        deck_b=DECK_FILLER,
        script=[
            # Choose TestPlayer as starting player, keep hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # T1: Play Mountain (hand alphabetical: Badlands=p10, LB=p11, LB=p12,
            # Memnite=p13, Mountain=p14, Plateau=p15, Taiga=p16).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p14"}},
            # Cast Lightning Bolt #1 (first playable spell).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # Target Opponent.
            {"name": "choose_action", "arguments": {"choice": "1"}},
            # Let the stack resolve — bolt deals 3 damage.
            {"name": "pass_priority", "arguments": {"until": "stack_resolved"}},
            {"name": "get_game_state", "arguments": {}},
        ],
        golden_name="stack_resolved",
        spectator=spectator_process,
    )
