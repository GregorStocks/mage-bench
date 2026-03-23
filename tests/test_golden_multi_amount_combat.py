"""Golden prompt test: GAME_GET_MULTI_AMOUNT combat damage distribution."""

from tests.golden_helpers import (
    DECK_GRIZZLY_BEARS,
    DECK_TWO_MEMNITES,
    run_golden_scenario,
)
from tests.golden_test_identities import golden_test


@golden_test("multi_amount_combat")
def test_multi_amount_combat(
    xmage_server,
    tmp_path,
    project_root,
    bridge_session,
    opponent_session,
    spectator_process,
):
    """Grizzly Bears (2/2) attacks into two Savannah Lions (2/1), triggering GAME_GET_MULTI_AMOUNT.

    Script:
    - P1 T1: Play Forest (can't cast Bears with only G mana).
    - P2 T1: Play Plains, cast Savannah Lions #1.
    - P1 T2: Play Forest, cast Grizzly Bears (1G).
    - P2 T2: Play Plains, cast Savannah Lions #2.
    - P1 T3: Skip land, attack with Grizzly Bears.
    - P2: Block with both Savannah Lions.
    - P1: Distribute 2 damage via GAME_GET_MULTI_AMOUNT -> amounts=[1,1].
    - All three creatures die (Bears takes 2+2=4 damage from Lions).
    """
    server, port = xmage_server
    run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "multi_amount_combat",
        deck_a=DECK_GRIZZLY_BEARS,
        deck_b=DECK_TWO_MEMNITES,
        script_a=[
            # Choose TestPlayer as starting player, keep hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # T1: Play Forest (Bears costs 1G = 2 mana, only have 1 Forest).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # T2: Play second Forest, cast Grizzly Bears (now have GG = 2 mana).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # Chain: Bears is now castable.
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # T3: Skip land play, go to combat.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # Declare attackers — attack with Grizzly Bears.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"attackers": "all"}},
            # Wait through blocking, then GAME_GET_MULTI_AMOUNT.
            {"name": "pass_priority", "arguments": {}},
            {
                "name": "assert_action",
                "arguments": {
                    "action_type": "GAME_GET_MULTI_AMOUNT",
                    "response_type": "multi_amount",
                },
            },
            {"name": "choose_action", "arguments": {"amounts": [1, 1]}},
            # Pass to postcombat main, capture final state.
            {"name": "pass_priority", "arguments": {"until": "postcombat_main"}},
            {"name": "get_game_state", "arguments": {}},
        ],
        script_b=[
            # Keep opening hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # P2's T1: Play Plains, cast Savannah Lions #1.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # P2's T2: Play Plains, cast Savannah Lions #2.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # P2 T2: Explicitly skip the attack so P1 reaches the intended combat prompt on T3.
            {"name": "pass_priority", "arguments": {}},
            {
                "name": "assert_action",
                "arguments": {"message_contains": "Select attackers"},
            },
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # Wait for P1's T3 attack -> declare blockers.
            {"name": "pass_priority", "arguments": {}},
            {
                "name": "assert_action",
                "arguments": {"message_contains": "Select blockers"},
            },
            # Block with both Savannah Lions against Grizzly Bears.
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "choose_action", "arguments": {"choice": "yes"}},
            # Stay alive until P1's script finishes.
            {"name": "pass_priority", "arguments": {}},
        ],
        golden_name="multi_amount_combat",
        bridge_a=bridge_session,
        bridge_b=opponent_session,
        spectator=spectator_process,
    )
