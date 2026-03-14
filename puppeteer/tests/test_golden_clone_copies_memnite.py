"""Golden prompt test: Clone copies Memnite."""

import pytest

from tests.golden_helpers import (
    DECK_CLONE_AND_MEMNITE,
    DECK_FILLER,
    run_golden_scenario,
)


@pytest.mark.golden
def test_clone_copies_memnite(
    xmage_server,
    tmp_path,
    project_root,
    bridge_session,
    opponent_session,
    spectator_process,
):
    """Clone enters as a copy of Memnite without extra cleanup actions."""
    server, port = xmage_server
    run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "clone_copies_memnite",
        deck_a=DECK_CLONE_AND_MEMNITE,
        deck_b=DECK_FILLER,
        script_a=[
            # Choose TestPlayer as starting player and keep opening hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # Opening hand (alphabetical): Black Lotus=p10, Clone=p11,
            # Island=p12..p15, Memnite=p16.
            #
            # Play Island, then use Black Lotus to power out Clone.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p12"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p10"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p16"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # Cast Clone, accept the copy effect, and capture once the board
            # reaches postcombat main. Avoid the old extra Island play.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p11"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "yes"}},
            {"name": "pass_priority", "arguments": {"until": "postcombat_main"}},
            {"name": "get_game_state", "arguments": {}},
        ],
        golden_name="clone_copies_memnite",
        bridge_a=bridge_session,
        bridge_b=opponent_session,
        spectator=spectator_process,
    )
