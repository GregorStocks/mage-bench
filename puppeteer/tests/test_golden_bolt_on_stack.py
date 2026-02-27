"""Golden prompt test: two Lightning Bolts on the stack."""

import pytest

from tests.golden_helpers import (
    DECK_BOLT_AND_BURN,
    DECK_FILLER,
    run_golden_scenario,
)


@pytest.mark.golden
def test_bolt_on_stack(xmage_server, tmp_path, project_root, bridge_session, potato_process):
    """Two Lightning Bolts on the stack, one targeting Memnite, one targeting Opponent.

    Script: choose starting player, keep hand, T1 play Mountain + cast Memnite,
    T2 play Badlands, skip attack, then cast two Lightning Bolts chained via
    choose_action (no pass_priority between casts) so both remain on the stack.
    """
    server, port = xmage_server
    run_golden_scenario(
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
            # T1: Play Mountain (Opponent's 7 Mountains = p3-p9; TestPlayer's hand
            # alphabetical: Badlands=p10, LB=p11, LB=p12, Memnite=p13, Mountain=p14,
            # Plateau=p15, Taiga=p16).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p14"}},
            # T1: Cast Memnite (0 mana).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p13"}},
            # T1: Pass with Lightning Bolt castable (save for T2 when we have 2 R sources).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # T2: Play Badlands for second R source.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p10"}},
            # T2: Declare attackers — skip (Memnite could attack but we decline).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # T2 Postcombat Main: cast Lightning Bolt #1.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}, "golden_blunder": True},
            # Target Opponent (chained choose_action, no pass_priority to avoid auto-pass resolving the bolt).
            {"name": "choose_action", "arguments": {"id": "p2"}},
            # Cast Lightning Bolt #2 while #1 is still on the stack.
            {"name": "choose_action", "arguments": {"index": 0}},
            # Target Memnite.
            {"name": "choose_action", "arguments": {"id": "p13"}},
            {"name": "get_game_state", "arguments": {}},
        ],
        golden_name="bolt_on_stack",
        bridge=bridge_session,
        potato=potato_process,
    )
