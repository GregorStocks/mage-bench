"""Golden prompt test: Lightning Bolt with stack_resolved yield."""

from tests.golden_helpers import (
    DECK_BOLT_AND_BURN,
    DECK_FILLER,
    run_golden_scenario,
)
from tests.golden_test_identities import golden_test


@golden_test("stack_resolved")
def test_stack_resolved(xmage_server, tmp_path, project_root, bridge_session, opponent_session, spectator_process):
    """Cast Lightning Bolt, then pass_priority(until="stack_resolved") to let it resolve.

    Script:
    - Choose TestPlayer as starting player, keep hand
    - T1: Play Mountain, cast Lightning Bolt targeting Opponent
    - pass_priority(until="stack_resolved") — bolt resolves
    - pass_priority(until="stack_resolved") again on an empty stack
      behaves like a normal one-pass priority advance
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
        script_a=[
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
            # Calling stack_resolved on an already empty stack should still pass
            # priority once, not return immediately.
            {"name": "pass_priority", "arguments": {"until": "stack_resolved"}},
            {"name": "get_game_state", "arguments": {}},
        ],
        golden_name="stack_resolved",
        bridge_a=bridge_session,
        bridge_b=opponent_session,
        spectator=spectator_process,
    )
