"""Golden prompt test: MDFC played as land and suspend card through resolution."""

import pytest

from tests.golden_helpers import (
    DECK_FILLER,
    DECK_MDFC_LAND_AND_SUSPEND,
    run_golden_scenario,
)


@pytest.mark.golden
def test_mdfc_land_and_suspend(xmage_server, tmp_path, project_root, bridge_session, potato_process, spectator_process):
    """Play Boggart Trawler as Boggart Bog (MDFC land mode) and suspend Crashing Footfalls,
    then play through until the last time counter is removed and it resolves into Rhino tokens.

    Opponent's 7 Mountains = p3-p9.  TestPlayer's hand (alphabetical):
    Boggart Trawler=p10, Crashing Footfalls=p11, Forest=p12, Plains=p13..p16.

    Script: choose starting player, keep hand, T1 play Forest + suspend
    Crashing Footfalls (4 time counters), T2 play Boggart Bog (MDFC back face),
    then advance through 4 upkeeps removing time counters until the last one
    triggers auto-cast from exile, resolving into two 4/4 Rhino creature tokens.
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
            # Advance through 4 upkeep counter removals.  Playing a Plains
            # each turn uses the land drop, so postcombat main has no playable
            # cards and pass_priority auto-advances through the entire rest of
            # the turn + opponent's turn in one call.
            #
            # T3 (upkeep: 4→3 counters): play Plains.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            # T4 (upkeep: 3→2 counters): play Plains.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            # T5 (upkeep: 2→1 counters): play Plains.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            # T6 (upkeep: 1→0 → suspend resolves): pass_priority stops at
            # GAME_ASK "Cast spell without paying its mana cost?"
            {"name": "pass_priority", "arguments": {}},
            # Answer yes to cast Crashing Footfalls from exile for free.
            {"name": "choose_action", "arguments": {"answer": True}},
            # Spell resolves (2x 4/4 Rhino tokens created), stops at main.
            {"name": "pass_priority", "arguments": {}},
            # Capture state: 2x 4/4 Rhino tokens + lands on battlefield,
            # Crashing Footfalls resolved (no longer in exile).
            {"name": "get_game_state", "arguments": {}},
        ],
        golden_name="mdfc_land_and_suspend",
        bridge=bridge_session,
        potato=potato_process,
        spectator=spectator_process,
    )
