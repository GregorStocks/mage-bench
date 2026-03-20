"""Golden prompt test: Mana Drain into Fact or Fiction."""

from tests.golden_helpers import (
    DECK_BLACK_LOTUS_DIVINATION,
    DECK_MANA_DRAIN_FOF,
    run_golden_scenario,
)
from tests.golden_test_identities import golden_test


@golden_test("mana_drain_fact_or_fiction")
def test_mana_drain_into_fact_or_fiction(
    xmage_server,
    tmp_path,
    project_root,
    bridge_session,
    opponent_session,
    spectator_process,
):
    """Mana Drain counters a Black Lotus-powered Divination, then casts Fact or Fiction.

    Script:
    - P1: Island, Black Lotus
    - P2: Black Lotus, cast Divination
    - P1: Mana Drain it
    - P1: cast Fact or Fiction with Mana Drain mana next turn
    - P2: split piles 3/2
    - P1: choose the 3-card pile
    """
    server, port = xmage_server
    run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "mana_drain_fact_or_fiction",
        deck_a=DECK_MANA_DRAIN_FOF,
        deck_b=DECK_BLACK_LOTUS_DIVINATION,
        script_a=[
            # Choose TestPlayer as starting player and keep opening hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # Opening hand (alphabetical): Black Lotus=p10, Fact or Fiction=p11,
            # Island=p12..p15, Mana Drain=p16.
            #
            # Play Island and Black Lotus so Mana Drain is live on turn 1.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p12"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p10"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # Move through our combat step, then stop when the opponent casts
            # Divination and Mana Drain becomes playable.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # Skip to our next precombat main (Mana Drain mana available).
            {"name": "pass_priority", "arguments": {"until": "my_turn"}},
            {"name": "pass_priority", "arguments": {"until": "precombat_main"}},
            # Cast Fact or Fiction using Mana Drain mana.
            {"name": "choose_action", "arguments": {"choice": "0", "mana_plan": "COLORLESS"}},
            # Choose the 3-card pile.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"pile": 1}},
            {"name": "get_game_state", "arguments": {}},
        ],
        script_b=[
            # Keep opening hand. Opponent hand (alphabetical):
            # Black Lotus=p3, Divination=p4, Plains=p5..p9.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # Turn 1: cast Black Lotus, then Divination off the Lotus mana.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p3"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p4"}},
            # Split piles 3/2 for Fact or Fiction (pick three cards for pile 1, then done).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # Stay alive until game ends.
            {"name": "pass_priority", "arguments": {}},
        ],
        golden_name="mana_drain_fact_or_fiction",
        bridge_a=bridge_session,
        bridge_b=opponent_session,
        spectator=spectator_process,
    )
