"""Golden prompt test: GAME_GET_MULTI_AMOUNT combat damage distribution.

Non-trivial scenario: Craw Wurm (6/4) attacks into Memnite (1/1) and
Phyrexian Walker (0/3), giving the attacker 6 damage to distribute
across blockers with different toughnesses (1 vs 3).
"""

import pytest

from tests.golden_helpers import (
    DECK_CRAW_WURM,
    DECK_MEMNITE_AND_WALKER,
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
    """Craw Wurm (6/4) attacks into Memnite (1/1) + Phyrexian Walker (0/3).

    Both blockers cost 0, so P2 casts them on T1 (no multi-turn land setup).

    Script:
    - P1 T1-T5: Play Forest each turn.
    - P2 T1: Play Forest, cast Memnite, cast Phyrexian Walker (both cost 0).
    - P1 T6: Play 6th Forest, cast Craw Wurm ({4}{G}{G}).
    - P1 T7: Attack with Craw Wurm.
    - P2: Block with both creatures.
    - P1: Distribute 6 damage via GAME_GET_MULTI_AMOUNT -> amounts=[3,3].
    """
    server, port = xmage_server
    run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "multi_amount_combat",
        deck_a=DECK_CRAW_WURM,
        deck_b=DECK_MEMNITE_AND_WALKER,
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
            {"name": "choose_action", "arguments": {"amounts": [3, 3]}},
            # Pass to postcombat main, capture final state.
            {"name": "pass_priority", "arguments": {"until": "postcombat_main"}},
            {"name": "get_game_state", "arguments": {}},
        ],
        script_b=[
            # Keep opening hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # P2 T1: Play Forest, cast Memnite (0), cast Phyrexian Walker (0).
            {"name": "pass_priority", "arguments": {"until": "end_of_turn"}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # P2 T2-T6: Skip turns. Memnite is summoning sick on T1 only.
            # pass_priority(until=end_of_turn) handles attacks by auto-passing.
            # Block Craw Wurm when P1 attacks (T7).
            {"name": "pass_priority", "arguments": {"until": "end_of_turn"}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            {"name": "pass_priority", "arguments": {"until": "end_of_turn"}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            {"name": "pass_priority", "arguments": {"until": "end_of_turn"}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            {"name": "pass_priority", "arguments": {"until": "end_of_turn"}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            {"name": "pass_priority", "arguments": {"until": "end_of_turn"}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # P1 T7: Block Craw Wurm with both creatures.
            {"name": "pass_priority", "arguments": {"until": "end_of_turn"}},
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
