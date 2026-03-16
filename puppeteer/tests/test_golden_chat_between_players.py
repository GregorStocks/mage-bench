"""Golden prompt test: players chatting to each other."""

import pytest

from tests.golden_helpers import (
    DECK_FILLER,
    run_golden_scenario,
)


@pytest.mark.golden
def test_chat_between_players(
    xmage_server,
    tmp_path,
    project_root,
    bridge_session,
    opponent_session,
    spectator_process,
):
    """Both players send chat messages and the pilot sees them in game log.

    Script:
    - P1 T1: Play Mountain, send "Good luck have fun!", pull events (advances cursor).
    - P2 T1: Play Mountain, send "You too, glhf!" (arrives at P1 with higher cursor).
    - P1 T2: Play Mountain, call get_game_log to capture chat in correct order.
    """
    server, port = xmage_server
    run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "chat_between_players",
        deck_a=DECK_FILLER,
        deck_b=DECK_FILLER,
        script_a=[
            # Choose TestPlayer as starting player, keep hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # T1: Play Mountain.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # Send chat after first land play, then pull events to advance
            # bridgeEventCursor. This ensures P2's reply (arriving during P2's turn)
            # gets a higher cursor and sorts after P1's message.
            {"name": "send_chat_message", "arguments": {"message": "Good luck have fun!"}},
            {"name": "get_game_log", "arguments": {"max_chars": 10000}},
            # Pass rest of T1 and let P2 take their turn.
            {"name": "pass_priority", "arguments": {"until": "my_turn"}},
            # T2: Play Mountain.
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # Check game log — should contain both players' chat messages.
            {"name": "get_game_log", "arguments": {"max_chars": 10000}},
            # Capture final state.
            {"name": "get_game_state", "arguments": {}},
        ],
        script_b=[
            # Keep opening hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # P2's T1: Play Mountain.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # Send chat back.
            {"name": "send_chat_message", "arguments": {"message": "You too, glhf!"}},
            # Stay alive until P1's script finishes.
            {"name": "pass_priority", "arguments": {}},
        ],
        golden_name="chat_between_players",
        bridge_a=bridge_session,
        bridge_b=opponent_session,
        spectator=spectator_process,
    )
