"""Golden prompt test: board cursor dedup eliminates redundant board payloads."""

import pytest

from tests.golden_helpers import (
    DECK_BOLT_AND_BURN,
    DECK_FILLER,
    run_golden_scenario,
)


@pytest.mark.golden
def test_board_cursor_dedup(xmage_server, tmp_path, project_root, bridge_session, opponent_session, spectator_process):
    """Verify board_unchanged=true when get_action_choices follows pass_priority.

    Script: choose starting player, keep hand, T1 play Mountain. After
    pass_priority returns with playable cards (board included), immediately
    call get_action_choices — the board hasn't changed, so it should return
    board_unchanged=true instead of the full board.
    """
    server, port = xmage_server
    run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "board_cursor_dedup",
        deck_a=DECK_BOLT_AND_BURN,
        deck_b=DECK_FILLER,
        script_a=[
            # Choose TestPlayer as starting player, keep hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # T1: Play Mountain.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p14"}},
            # T1 main: pass_priority returns with playable cards (full board).
            {"name": "pass_priority", "arguments": {}},
            # Redundant get_action_choices — board is unchanged, should dedup.
            {"name": "get_action_choices", "arguments": {}},
            # Now act on the choices.
            {"name": "choose_action", "arguments": {"choice": "no"}},
        ],
        golden_name="board_cursor_dedup",
        bridge_a=bridge_session,
        bridge_b=opponent_session,
        spectator=spectator_process,
    )
