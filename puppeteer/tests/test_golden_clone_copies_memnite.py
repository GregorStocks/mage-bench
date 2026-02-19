"""Golden prompt test: Clone copies Memnite."""

import pytest

from tests.golden_helpers import (
    DECK_CLONE_AND_MEMNITE,
    DECK_FILLER,
    assert_golden_prompt,
    run_golden_scenario,
)


@pytest.mark.golden
def test_clone_copies_memnite(xmage_server, tmp_path, project_root):
    """Clone enters as a copy of Memnite — verifies copy effect representation."""
    server, port = xmage_server
    prompt = run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "clone_copies_memnite",
        deck_a=DECK_CLONE_AND_MEMNITE,
        deck_b=DECK_FILLER,
        script=[
            # Choose TestPlayer as starting player and keep opening hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # Play Island (alphabetical: Black Lotus=p3, Island=p4..p7, Memnite=p8).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p4"}},
            # Next turn: cast Black Lotus then Memnite.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p3"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p8"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            # Cast Clone, choose to copy, target Memnite, then capture state.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p10"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": True}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "get_game_state", "arguments": {}},
        ],
    )
    assert_golden_prompt("clone_copies_memnite", prompt)
