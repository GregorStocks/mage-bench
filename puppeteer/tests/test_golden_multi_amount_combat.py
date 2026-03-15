"""Golden prompt test: GAME_GET_MULTI_AMOUNT combat damage distribution.

Non-trivial scenario: Craw Wurm (6/4) attacks into Savannah Lions (2/1)
and Durkwood Boars (4/4), giving the attacker 6 damage to distribute
meaningfully across blockers with different toughnesses.
"""

import pytest

from tests.golden_helpers import (
    DECK_CRAW_WURM,
    DECK_LIONS_AND_BOARS,
    run_golden_scenario,
)


@pytest.mark.golden
def test_multi_amount_combat(
    xmage_server,
    tmp_path,
    project_root,
    bridge_session,
    opponent_session,
    spectator_process,
):
    """Craw Wurm (6/4) attacks into Savannah Lions (2/1) + Durkwood Boars (4/4).

    Script:
    - P1 T1-T5: Play Forest each turn (need 6 mana for Craw Wurm {4}{G}{G}).
    - P2 T1: Play Plains, cast Savannah Lions.
    - P2 T2-T4: Play Forest, skip attack.
    - P2 T5: Play Forest, cast Durkwood Boars ({4}{G}), skip attack.
    - P2 T6: Play Forest, skip attack (both creatures ready).
    - P1 T6: Play 6th Forest, cast Craw Wurm.
    - P1 T7: Attack with Craw Wurm.
    - P2: Block with Savannah Lions and Durkwood Boars.
    - P1: Distribute 6 damage via GAME_GET_MULTI_AMOUNT -> amounts=[2,4].
    - All three creatures die (Craw Wurm takes 2+4=6 damage, toughness 4).
    """
    server, port = xmage_server
    run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "multi_amount_combat",
        deck_a=DECK_CRAW_WURM,
        deck_b=DECK_LIONS_AND_BOARS,
        script_a=[
            # Choose TestPlayer as starting player, keep hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # T1: Play Forest.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # T2: Play Forest.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # T3: Play Forest.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # T4: Play Forest.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # T5: Play Forest.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # T6: Play 6th Forest, cast Craw Wurm ({4}{G}{G}).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # T7: Skip land, go to combat, attack with Craw Wurm.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"attackers": "all"}},
            # Wait through blocking, then distribute 6 combat damage.
            {"name": "pass_priority", "arguments": {}},
            {
                "name": "assert_action",
                "arguments": {"action_type": "GAME_GET_MULTI_AMOUNT", "response_type": "multi_amount"},
            },
            {"name": "choose_action", "arguments": {"amounts": [2, 4]}},
            # Pass to postcombat main, capture final state.
            {"name": "pass_priority", "arguments": {"until": "postcombat_main"}},
            {"name": "get_game_state", "arguments": {}},
        ],
        script_b=[
            # Keep opening hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # P2 T1: Play Plains, cast Savannah Lions.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # P2 T2: Play Forest, skip attack (Lions no longer summoning sick).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "assert_action", "arguments": {"message_contains": "Select attackers"}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # P2 T3: Play Forest, skip attack.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "assert_action", "arguments": {"message_contains": "Select attackers"}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # P2 T4: Play Forest, skip attack.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "assert_action", "arguments": {"message_contains": "Select attackers"}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # P2 T5: Play Forest, cast Durkwood Boars ({4}{G}), skip attack.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "assert_action", "arguments": {"message_contains": "Select attackers"}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # P2 T6: Play Forest, skip attack (both creatures ready).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "assert_action", "arguments": {"message_contains": "Select attackers"}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # P1 T7: Block Craw Wurm with both creatures.
            {"name": "pass_priority", "arguments": {}},
            {"name": "assert_action", "arguments": {"message_contains": "Select blockers"}},
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
