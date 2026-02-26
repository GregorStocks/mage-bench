"""Golden prompt test: MDFC played as land and suspend card."""

import pytest

from tests.golden_helpers import (
    DECK_FILLER,
    DECK_MDFC_LAND_AND_SUSPEND,
    run_golden_scenario,
)


@pytest.mark.golden
def test_mdfc_land_and_suspend(xmage_server, tmp_path, project_root, bridge_session, potato_process):
    """Play Boggart Trawler as Boggart Bog (MDFC land mode) and suspend Crashing Footfalls.

    Opponent's 7 Mountains = p3-p9.  TestPlayer's hand (alphabetical):
    Boggart Trawler=p10, Crashing Footfalls=p11, Forest=p12, Plains=p13..p16.

    Script: choose starting player, keep hand, T1 play Forest + suspend
    Crashing Footfalls, T2 play Boggart Bog (back face of Boggart Trawler MDFC).
    """
    server, port = xmage_server
    run_golden_scenario(
        server=server,
        port=port,
        project_root=project_root,
        game_dir=tmp_path / "mdfc_land_and_suspend",
        deck_a=DECK_MDFC_LAND_AND_SUSPEND,
        deck_b=DECK_FILLER,
        script=[
            # Choose TestPlayer as starting player, keep hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # T1: Play Forest (p12).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p12"}},
            # T1: Suspend Crashing Footfalls (p11) paying {G}.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p11"}},
            # T2: Play Boggart Trawler as Boggart Bog (p10).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p10"}},
            # GAME_CHOOSE_ABILITY: select "Play Boggart Bog" (only option).
            {"name": "choose_action", "arguments": {"index": 0}},
            # Boggart Bog ETB: "you may pay 3 life" — decline, enters tapped.
            {"name": "choose_action", "arguments": {"answer": False}},
            # Capture state: Forest + Boggart Bog on battlefield,
            # Crashing Footfalls in exile with time counters.
            {"name": "get_game_state", "arguments": {}},
        ],
        golden_name="mdfc_land_and_suspend",
        bridge=bridge_session,
        potato=potato_process,
    )
