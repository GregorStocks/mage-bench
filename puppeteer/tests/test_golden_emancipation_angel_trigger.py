"""Golden prompt test: Emancipation Angel ETB target prompt."""

import pytest

from tests.golden_helpers import (
    DECK_EMANCIPATION_ANGEL,
    DECK_FILLER,
    run_golden_scenario,
)


@pytest.mark.golden
def test_emancipation_angel_trigger(
    xmage_server,
    tmp_path,
    project_root,
    bridge_session,
    opponent_session,
    spectator_process,
):
    """Capture the prompt when Emancipation Angel's ETB trigger asks for a target."""
    server, port = xmage_server
    run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "emancipation_angel_trigger",
        deck_a=DECK_EMANCIPATION_ANGEL,
        deck_b=DECK_FILLER,
        script_a=[
            # Choose TestPlayer as starting player, keep hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # Opponent's opening hand is Mountain p3-p9.
            # TestPlayer's opening hand alphabetical: Black Lotus=p10,
            # Emancipation Angel=p11, Plains=p12-p16.
            # T1: Play Plains.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p12"}},
            # Cast Black Lotus.
            {"name": "choose_action", "arguments": {"choice": "p10"}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            # Cast Emancipation Angel, then pass so it resolves and the
            # script stops on the trigger's target-selection prompt.
            {"name": "choose_action", "arguments": {"choice": "p11"}},
            {"name": "pass_priority", "arguments": {}},
        ],
        golden_name="emancipation_angel_trigger",
        bridge_a=bridge_session,
        bridge_b=opponent_session,
        spectator=spectator_process,
    )
