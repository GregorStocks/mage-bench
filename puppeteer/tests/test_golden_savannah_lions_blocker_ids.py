"""Golden prompt test: blocker prompt renders incoming attacker IDs."""

from tests.golden_helpers import (
    DECK_SAVANNAH_LIONS,
    run_golden_scenario,
)
from tests.golden_test_identities import golden_test


@golden_test("savannah_lions_blocker_ids")
def test_savannah_lions_blocker_ids(
    xmage_server,
    tmp_path,
    project_root,
    bridge_session,
    opponent_session,
    spectator_process,
):
    """Player A blocks a lone Savannah Lions after choosing to draw first."""
    server, port = xmage_server
    run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "savannah_lions_blocker_ids",
        deck_a=DECK_SAVANNAH_LIONS,
        deck_b=DECK_SAVANNAH_LIONS,
        script_a=[
            # Let Opponent play first, then keep.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "1"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # T1: Play Plains and cast Savannah Lions.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # Wait for Opponent's T2 attack, then block the lone attacker.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "choose_action", "arguments": {"choice": "yes"}},
            # Capture postcombat state after the trade.
            {"name": "pass_priority", "arguments": {"until": "postcombat_main"}},
            {"name": "get_game_state", "arguments": {}},
        ],
        script_b=[
            # Keep opening hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # T1: Play Plains and cast Savannah Lions.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # T2: Play Plains, then attack with Savannah Lions.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"attackers": "all"}},
            # Stay alive until Player A finishes.
            {"name": "pass_priority", "arguments": {}},
        ],
        golden_name="savannah_lions_blocker_ids",
        bridge_a=bridge_session,
        bridge_b=opponent_session,
        spectator=spectator_process,
    )
