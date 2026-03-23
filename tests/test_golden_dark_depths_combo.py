"""Golden prompt test: Dark Depths combo with Marit Lage lethal attack."""

from tests.golden_helpers import (
    DECK_DARK_DEPTHS_COMBO,
    DECK_FILLER,
    run_golden_scenario,
)
from tests.golden_test_identities import golden_test


@golden_test("dark_depths_combo")
def test_dark_depths_combo(
    xmage_server,
    tmp_path,
    project_root,
    bridge_session,
    opponent_session,
    spectator_process,
):
    """Dark Depths + Thespian's Stage combo into Marit Lage lethal attack.

    Opponent's 7 Mountains = p3-p9. TestPlayer's hand (alphabetical):
    Black Lotus=p10, Dark Depths=p11, Plains=p12..p15, Thespian's Stage=p16.

    Script: choose starting player, keep hand, play Dark Depths T1,
    play Thespian's Stage + Black Lotus T2, activate Stage copying
    Dark Depths (legend rule + state trigger creates Marit Lage),
    then attack on the next turn.
    """
    server, port = xmage_server
    run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "dark_depths_combo",
        deck_a=DECK_DARK_DEPTHS_COMBO,
        deck_b=DECK_FILLER,
        script_a=[
            # Choose TestPlayer as starting player, keep hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "no"}},
            # Turn 1: Play Dark Depths (p11, enters with 10 ice counters).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p11"}},
            # Turn 2: Play Thespian's Stage (p16), then Black Lotus (p10)
            # so the combo does not need extra land-drop turns.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p16"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p10"}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "0"}},
            # Activate Thespian's Stage. Auto-tap spends Black Lotus for {2}.
            {"name": "pass_priority", "arguments": {}},
            {
                "name": "choose_action",
                "arguments": {"choice": "p16"},
                "golden_blunder": True,
            },
            # GAME_CHOOSE_ABILITY: index 1 = "{2}, {T}: copy target land."
            # (index 0 is the mana ability "{T}: Add {C}.")
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "1"}},
            # Target the original Dark Depths (p11) for the copy.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "p11"}},
            # Legend rule: "Select a Dark Depths to keep" — keep the copy
            # (index 1 = p9, 0 ice counters). State trigger then creates
            # Marit Lage (20/20 flying indestructible).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"choice": "1"}},
            # Jump straight to the next attack step instead of spending a turn
            # on extra Plains plays.
            {"name": "pass_priority", "arguments": {"until": "my_turn"}},
            {"name": "pass_priority", "arguments": {"until": "declare_attackers"}},
            {
                "name": "choose_action",
                "arguments": {"attackers": "all"},
                "golden_blunder": True,
            },
            # Capture state with Marit Lage attacking before combat damage.
            {"name": "get_game_state", "arguments": {}},
            # Pass priority — combat damage resolves, Opponent dies (game_over).
            {"name": "pass_priority", "arguments": {}},
        ],
        golden_name="dark_depths_combo",
        bridge_a=bridge_session,
        bridge_b=opponent_session,
        spectator=spectator_process,
    )
