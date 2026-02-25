"""Golden test: Ancient Stirrings triggers lookedAt zone short ID conflict."""

import pytest

from tests.golden_helpers import (
    DECK_ANCIENT_STIRRINGS,
    DECK_FILLER,
    run_golden_scenario,
)


@pytest.mark.golden
def test_ancient_stirrings_short_id_conflict(xmage_server, tmp_path, project_root, bridge_session, potato_process):
    """Cast Ancient Stirrings, select a card from lookedAt zone, verify no ID conflicts.

    Reproduces bug where cards in lookedAt get local short IDs (findCardViewById
    doesn't search lookedAt), then register() throws when the card appears in
    hand with a different server-assigned ID.

    Script: choose starting player, keep hand, T1 play Forest + cast Ancient
    Stirrings, answer yes to reveal, select first colorless card, get_game_state.
    """
    server, port = xmage_server
    prompt = run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "ancient_stirrings",
        deck_a=DECK_ANCIENT_STIRRINGS,
        deck_b=DECK_FILLER,
        script=[
            # Choose TestPlayer as starting player, keep hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # T1: Play Forest (TestPlayer's hand alphabetical:
            # Ancient Stirrings=p10, Forest=p11, Plains=p12-p16).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p11"}},
            # Cast Ancient Stirrings (chain after land play).
            {"name": "choose_action", "arguments": {"id": "p10"}},
            # Pass priority — Ancient Stirrings resolves.
            # GAME_ASK: "Reveal a colorless card and put it into your hand?"
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": True}},
            # GAME_TARGET: Select which colorless card from the top 5.
            # This is where cards in lookedAt zone get local IDs (the bug trigger).
            {"name": "choose_action", "arguments": {"index": 0}},
            # Get game state — the selected card moved from lookedAt to hand.
            # Before fix: triggers "UUID already mapped to different short ID" error.
            {"name": "get_game_state", "arguments": {}},
        ],
        golden_name="ancient_stirrings_short_id",
        bridge=bridge_session,
        potato=potato_process,
    )

    # Assert no short ID conflict errors in any tool result.
    for msg in prompt:
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            assert "UUID already mapped to different short ID" not in content, (
                f"Short ID conflict in tool result: {content[:300]}"
            )
            assert "Short ID already mapped to different UUID" not in content, (
                f"Short ID conflict in tool result: {content[:300]}"
            )
