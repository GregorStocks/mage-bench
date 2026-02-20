"""Golden prompt test: Savannah Lions trade in combat."""

import pytest

from tests.golden_helpers import (
    DECK_SAVANNAH_LIONS,
    assert_golden_prompt,
    run_golden_scenario_two_replay,
)


@pytest.mark.golden
def test_savannah_lions_trade(xmage_server, tmp_path, project_root):
    """Both players play Savannah Lions, P1 attacks T2, P2 blocks, both die.

    Deck hand (alphabetical IDs): Plains x6 = p3-p8, Savannah Lions = p9.

    Script:
    - P1 T1: Play Plains, chain cast Savannah Lions (no pass_priority between).
    - P2 T1: Play Plains, chain cast Savannah Lions.
    - P1 T2: Skip land, attack with Savannah Lions (no longer summoning sick).
    - P2: Block with Savannah Lions -> both 2/1 creatures trade.
    - Capture state at postcombat main with both Lions in graveyards.
    """
    server, port = xmage_server
    prompt = run_golden_scenario_two_replay(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "savannah_lions_trade",
        deck_a=DECK_SAVANNAH_LIONS,
        deck_b=DECK_SAVANNAH_LIONS,
        script_a=[
            # Choose TestPlayer as starting player, keep hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # T1: Play Plains (pass_priority lands at postcombat after auto-pass).
            # Plains x6 = p3-p8, Savannah Lions = p9.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p3"}},
            # T1: Cast Savannah Lions immediately (chained choose_action, no
            # pass_priority) so Lions enters on T1 and loses summoning sickness
            # by T2.
            {"name": "choose_action", "arguments": {"id": "p9"}},
            # T2: Precombat main — skip land play.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # T2: Declare attackers — attack with Savannah Lions.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"attackers": ["all"]}},
            # Pass through combat (P2 blocks) to postcombat main.
            {"name": "pass_priority", "arguments": {"until": "postcombat_main"}},
            # Capture final state: both Lions should be in graveyards.
            {"name": "get_game_state", "arguments": {}},
        ],
        script_b=[
            # Keep opening hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # P2's T1: Play Plains.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            # P2's T1: Cast Savannah Lions (chained, no pass_priority).
            {"name": "choose_action", "arguments": {"index": 0}},
            # Wait for P1's T2 attack -> declare blockers.
            # P2's registry: p1-p6=Plains, p7=Savannah Lions, p8=drawn Plains,
            # p9=P1's Plains, p10=P1's Savannah Lions.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"blockers": ["p7:p10"]}},
            # Stay alive until P1's script finishes.
            {"name": "pass_priority", "arguments": {}},
        ],
    )
    assert_golden_prompt("savannah_lions_trade", prompt)
