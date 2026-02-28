"""Golden prompt test: Mana Drain into Fact or Fiction."""

import pytest

from tests.golden_helpers import (
    DECK_MANA_DRAIN_FOF,
    DECK_PLAINS_LIONS,
    run_golden_scenario_two_replay,
)


@pytest.mark.golden
def test_mana_drain_into_fact_or_fiction(xmage_server, tmp_path, project_root, spectator_process):
    """Mana Drain counters Savannah Lions, then Fact or Fiction off the mana.

    Script:
    - P1: Island, Island
    - P2: Plains, cast Savannah Lions
    - P1: Mana Drain it
    - P1: Island, cast Fact or Fiction with Mana Drain mana
    - P2: split piles 3/2
    - P1: choose the 3-card pile
    """
    server, port = xmage_server
    run_golden_scenario_two_replay(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "mana_drain_fact_or_fiction",
        deck_a=DECK_MANA_DRAIN_FOF,
        deck_b=DECK_PLAINS_LIONS,
        script_a=[
            # Choose TestPlayer as starting player and keep opening hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # Play Island (only playable card).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            # Cast Sol Ring.
            {"name": "choose_action", "arguments": {"index": 0}},
            # Turn 2: play second Island before opponent casts Savannah Lions.
            {"name": "pass_priority", "arguments": {"until": "my_turn"}},
            {"name": "pass_priority", "arguments": {"until": "precombat_main"}},
            {"name": "choose_action", "arguments": {"index": 0}},
            # End turn, wait for opponent to cast Savannah Lions.
            {"name": "pass_priority", "arguments": {"until": "end_of_turn"}},
            # Counter Savannah Lions with Mana Drain.
            {"name": "choose_action", "arguments": {"index": 0}},
            # Skip to our next precombat main (Mana Drain mana available).
            {"name": "pass_priority", "arguments": {"until": "my_turn"}},
            {"name": "pass_priority", "arguments": {"until": "precombat_main"}},
            # Cast Fact or Fiction using Mana Drain mana.
            {"name": "choose_action", "arguments": {"index": 0, "mana_plan": ["COLORLESS"]}},
            # Choose the 3-card pile.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"pile": 1}},
            {"name": "get_game_state", "arguments": {}},
        ],
        script_b=[
            # Keep opening hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # Turn 1: play Plains.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {"until": "end_of_turn"}},
            # Turn 2: cast Savannah Lions (only playable card).
            {"name": "pass_priority", "arguments": {"until": "precombat_main"}},
            {"name": "choose_action", "arguments": {"index": 0}},
            # Split piles 3/2 for Fact or Fiction (pick three cards for pile 1, then done).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # Stay alive until game ends.
            {"name": "pass_priority", "arguments": {}},
        ],
        golden_name="mana_drain_fact_or_fiction",
        spectator=spectator_process,
    )
