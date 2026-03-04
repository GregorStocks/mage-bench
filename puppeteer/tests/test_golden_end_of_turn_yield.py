"""Golden prompt test: pass_priority until=end_of_turn skips playable cards."""

import pytest

from tests.golden_helpers import (
    DECK_BOLT_AND_BURN,
    DECK_FILLER,
    run_golden_scenario,
)


@pytest.mark.golden
def test_end_of_turn_yield(
    xmage_server,
    tmp_path,
    project_root,
    bridge_session,
    opponent_session,
    spectator_process,
):
    """pass_priority(until=end_of_turn) skips main phase with playable cards.

    Script: T1 play Mountain, then pass_priority(until="end_of_turn").
    Lightning Bolt and Memnite are playable but end_of_turn should skip
    past them, advancing to the END_TURN step before returning.
    """
    server, port = xmage_server
    run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "end_of_turn_yield",
        deck_a=DECK_BOLT_AND_BURN,
        deck_b=DECK_FILLER,
        script_a=[
            # Choose TestPlayer as starting player, keep hand.
            # Opponent's 7 Mountains = p3-p9; TestPlayer's hand alphabetical:
            # Badlands=p10, LB=p11, LB=p12, Memnite=p13, Mountain=p14,
            # Plateau=p15, Taiga=p16.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # T1: Play Mountain — Lightning Bolt and Memnite become playable.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p14"}},
            # Skip to end of turn — should auto-pass through main phase
            # despite Lightning Bolt and Memnite being playable.
            {"name": "pass_priority", "arguments": {"until": "end_of_turn"}},
            # Capture game state — should be at END_TURN step with
            # Lightning Bolt still in hand (not auto-cast).
            {"name": "get_game_state", "arguments": {}},
        ],
        golden_name="end_of_turn_yield",
        bridge_a=bridge_session,
        bridge_b=opponent_session,
        spectator=spectator_process,
    )
