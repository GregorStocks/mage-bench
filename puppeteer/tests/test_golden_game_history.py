"""Golden prompt test: get_game_history returns structured per-turn actions."""

import pytest

from tests.golden_helpers import (
    DECK_BOLT_AND_BURN,
    DECK_FILLER,
    run_golden_scenario,
)


@pytest.mark.golden
def test_game_history(xmage_server, tmp_path, project_root):
    """Play two turns with meaningful actions, then call get_game_history.

    Script: choose starting player, keep hand, T1 play Mountain + cast Memnite,
    pass through opponent's turn, T2 play Badlands + cast Lightning Bolt at
    opponent. Then call get_game_history to capture structured per-turn output.
    """
    server, port = xmage_server
    run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "game_history",
        deck_a=DECK_BOLT_AND_BURN,
        deck_b=DECK_FILLER,
        script=[
            # Choose TestPlayer as starting player, keep hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # T1: Play Mountain.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p14"}},
            # T1: Cast Memnite (0 mana).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p13"}},
            # T1: Pass with Lightning Bolt castable.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # T2: Play Badlands for second R source.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p10"}},
            # T2: Declare attackers — skip.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # T2 Postcombat Main: cast Lightning Bolt.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            # Target Opponent.
            {"name": "choose_action", "arguments": {"id": "p2"}},
            # Let the bolt resolve, then get structured history.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            {"name": "get_game_history", "arguments": {}},
        ],
        golden_name="game_history",
    )
