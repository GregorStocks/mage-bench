"""Golden prompt test: Dark Depths combo with Marit Lage lethal attack."""

import pytest

from tests.golden_helpers import (
    DECK_DARK_DEPTHS_COMBO,
    DECK_FILLER,
    run_golden_scenario,
)


@pytest.mark.golden
def test_dark_depths_combo(xmage_server, tmp_path, project_root):
    """Dark Depths + Thespian's Stage combo into Marit Lage lethal attack.

    Opponent's 7 Mountains = p3-p9. TestPlayer's hand (alphabetical):
    Dark Depths=p10, Plains=p11..p15, Thespian's Stage=p16.

    Script: choose starting player, keep hand, play Plains T1/T2,
    play Dark Depths T3, play Thespian's Stage T4, activate Stage
    copying Dark Depths (legend rule + state trigger creates Marit Lage),
    attack T5 for lethal.
    """
    server, port = xmage_server
    run_golden_scenario(
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
            # Turn 1: Play Plains (p11).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p11"}},
            # Turn 2: Play Plains (p12).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p12"}},
            # Turn 3: Play Dark Depths (p10, enters with 10 ice counters).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p10"}},
            # Turn 4: Play Thespian's Stage (p16).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p16"}},
            # T4 combat: Activate Thespian's Stage.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p16"}},
            # GAME_CHOOSE_ABILITY: index 1 = "{2}, {T}: copy target land."
            # (index 0 is the mana ability "{T}: Add {C}.")
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 1}},
            # Target Dark Depths (p10) for the copy.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p10"}},
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
        golden_name="dark_depths_combo",
    )
