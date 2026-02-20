"""Golden prompt test: Mana Drain into Fact or Fiction."""

import pytest

from tests.golden_helpers import (
    DECK_MANA_DRAIN_FOF,
    DECK_PLAINS_LIONS,
    assert_golden_prompt,
    run_golden_scenario_two_replay,
)


@pytest.mark.golden
def test_mana_drain_into_fact_or_fiction(xmage_server, tmp_path, project_root):
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
    prompt = run_golden_scenario_two_replay(
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
            # Play Island (alphabetical: Fact or Fiction=p3, Island=p4..p8, Mana Drain=p9).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p4"}},
            # Turn 2: play Island.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p5"}},
            # Counter Savannah Lions with Mana Drain.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p9"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            # Turn 3: play Island, cast Fact or Fiction.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p6"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p3"}},
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
            # Turn 2: cast Savannah Lions (Plains x5 first, then Lions).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 5}},
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
    )
    assert_golden_prompt("mana_drain_fact_or_fiction", prompt)
