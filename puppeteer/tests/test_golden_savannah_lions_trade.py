"""Golden prompt test: Savannah Lions trade in combat."""

import pytest

from tests.golden_helpers import (
    DECK_SAVANNAH_LIONS,
    run_golden_scenario_two_replay,
)


@pytest.mark.golden
def test_savannah_lions_trade(xmage_server, tmp_path, project_root, spectator_process):
    """Both players play Savannah Lions, P1 attacks T2, P2 blocks, both die.

    Script:
    - P1 T1: Play Plains, chain cast Savannah Lions (no pass_priority between).
    - P2 T1: Play Plains, chain cast Savannah Lions.
    - P1 T2: Skip land, attack with Savannah Lions (no longer summoning sick).
    - P2: Block with Savannah Lions -> both 2/1 creatures trade.
    - Capture state at postcombat main with both Lions in graveyards.
    """
    server, port = xmage_server
    run_golden_scenario_two_replay(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "savannah_lions_trade",
        deck_a=DECK_SAVANNAH_LIONS,
        deck_b=DECK_SAVANNAH_LIONS,
        script_a=[
            # Choose TestPlayer as starting player, keep hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # T1: Play Plains (first land choice).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # T1: Cast Savannah Lions immediately (only castable spell after land).
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # T2: Precombat main — skip land play.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # T2: Declare attackers — attack with Savannah Lions.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"attackers": "all"}},
            # Mid-game history check: should show land plays, casts, and attack.
            {"name": "get_game_history", "arguments": {}},
            # Pass through combat (P2 blocks) to postcombat main.
            {"name": "pass_priority", "arguments": {"until": "postcombat_main"}},
            # Capture final state: both Lions should be in graveyards.
            {"name": "get_game_state", "arguments": {}},
        ],
        script_b=[
            # Keep opening hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # P2's T1: Play Plains.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # P2's T1: Cast Savannah Lions (chained, no pass_priority).
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # Wait for P1's T2 attack -> declare blockers.
            {"name": "pass_priority", "arguments": {}},
            # Declare lone Savannah Lions blocker against lone attacker using indexes.
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "choose_action", "arguments": {"choice": "yes"}},
            # Stay alive until P1's script finishes.
            {"name": "pass_priority", "arguments": {}},
        ],
        golden_name="savannah_lions_trade",
        spectator=spectator_process,
    )
