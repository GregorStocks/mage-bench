"""Golden prompt test: Dark Depths combo with Marit Lage lethal attack."""

import pytest

from tests.golden_helpers import (
    DECK_DARK_DEPTHS_COMBO,
    DECK_FILLER,
    run_golden_scenario,
)


@pytest.mark.golden
def test_dark_depths_combo(xmage_server, tmp_path, project_root, bridge_session, potato_process, spectator_process):
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
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # Turn 1: Play Plains (p11).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p11"}},
            # Turn 2: Play Plains (p12).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p12"}},
            # Turn 3: Play Dark Depths (p10, enters with 10 ice counters).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p10"}},
            # Turn 4: Play Thespian's Stage (p16).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p16"}},
            # T4 combat: Activate Thespian's Stage.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p16"}, "golden_blunder": True},
            # GAME_CHOOSE_ABILITY: index 1 = "{2}, {T}: copy target land."
            # (index 0 is the mana ability "{T}: Add {C}.")
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "1"}},
            # Target Dark Depths (p10) for the copy.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p10"}},
            # Legend rule: "Select a Dark Depths to keep" — keep the copy
            # (index 1 = p9, 0 ice counters). State trigger then creates
            # Marit Lage (20/20 flying indestructible).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "1"}},
            # Turn 5: pass precombat main to reach combat.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # Declare Marit Lage as attacker for lethal (20 damage).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"attackers": "all"}, "golden_blunder": True},
            # Capture state with Marit Lage attacking before combat damage.
            {"name": "get_game_state", "arguments": {}},
            # Pass priority — combat damage resolves, Opponent dies (game_over).
            {"name": "pass_priority", "arguments": {}},
        ],
        golden_name="dark_depths_combo",
        bridge=bridge_session,
        potato=potato_process,
        spectator=spectator_process,
    )
