"""Golden prompt test: Dark Depths combo with Marit Lage lethal attack."""

import pytest

from tests.golden_helpers import (
    DECK_DARK_DEPTHS_COMBO,
    DECK_FILLER,
    assert_golden_prompt,
    run_golden_scenario,
)


@pytest.mark.golden
def test_dark_depths_combo(xmage_server, tmp_path, project_root):
    """Dark Depths + Thespian's Stage combo into Marit Lage lethal attack.

    Deck hand (alphabetical IDs): Dark Depths=p3, Plains=p4..p8,
    Thespian's Stage=p9.

    Script: choose starting player, keep hand, play Plains T1/T2,
    play Dark Depths T3, play Thespian's Stage T4, activate Stage
    copying Dark Depths (legend rule + state trigger creates Marit Lage),
    attack T5 for lethal.
    """
    server, port = xmage_server
    prompt = run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "dark_depths_combo",
        deck_a=DECK_DARK_DEPTHS_COMBO,
        deck_b=DECK_FILLER,
        script=[
            # Choose TestPlayer as starting player, keep hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # Turn 1: Play Plains (p4).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p4"}},
            # Turn 2: Play Plains (p5).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p5"}},
            # Turn 3: Play Dark Depths (p3, enters with 10 ice counters).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p3"}},
            # Turn 4: Play Thespian's Stage (p9).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p9"}},
            # T4 combat: Activate Thespian's Stage.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p9"}},
            # GAME_CHOOSE_ABILITY: index 1 = "{2}, {T}: copy target land."
            # (index 0 is the mana ability "{T}: Add {C}.")
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 1}},
            # Target Dark Depths (p3) for the copy.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p3"}},
            # Legend rule: "Select a Dark Depths to keep" — keep the copy
            # (index 1 = p9, 0 ice counters). State trigger then creates
            # Marit Lage (20/20 flying indestructible).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 1}},
            # Turn 5: pass precombat main to reach combat.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # Declare Marit Lage as attacker for lethal (20 damage).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"attackers": ["all"]}},
            # Capture final state.
            {"name": "get_game_state", "arguments": {}},
        ],
    )
    assert_golden_prompt("dark_depths_combo", prompt)
