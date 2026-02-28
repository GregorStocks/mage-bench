"""Scenario definitions for non-persistent (subprocess) golden tests.

Each scenario carries unique player/spectator names for XMage server isolation,
allowing all scenarios to run concurrently on a shared server. A ``name_map``
property maps runtime names back to canonical names so golden files stay stable.
"""

from __future__ import annotations

import dataclasses

from tests.golden_helpers import (
    DECK_BOLT_AND_BURN,
    DECK_FILLER,
    DECK_MANA_DRAIN_FOF,
    DECK_PLAINS_LIONS,
    DECK_SAVANNAH_LIONS,
)


@dataclasses.dataclass(frozen=True)
class Scenario:
    golden_name: str
    deck_a: str
    deck_b: str
    script_a: list[dict]
    script_b: list[dict] | None  # None for potato-opponent (run_golden_scenario)
    player_a_name: str
    player_b_name: str
    spectator_name: str

    @property
    def name_map(self) -> dict[str, str]:
        """Map runtime names to canonical golden file names."""
        m: dict[str, str] = {}
        if self.player_a_name != "TestPlayer":
            m[self.player_a_name] = "TestPlayer"
        if self.player_b_name != "Opponent":
            m[self.player_b_name] = "Opponent"
        return m


SUBPROCESS_SCENARIOS: list[Scenario] = [
    # --- Mana Drain into Fact or Fiction ---
    Scenario(
        golden_name="mana_drain_fact_or_fiction",
        deck_a=DECK_MANA_DRAIN_FOF,
        deck_b=DECK_PLAINS_LIONS,
        player_a_name="Player_MD",
        player_b_name="Opp_MD",
        spectator_name="spec_md",
        script_a=[
            # Choose starting player and keep opening hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # Play Island (only playable card).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            # Cast Sol Ring.
            {"name": "choose_action", "arguments": {"index": 0}},
            # Turn 2: play second Island before opponent casts Savannah Lions.
            {"name": "pass_priority", "arguments": {"until": "my_turn"}},
            {"name": "pass_priority", "arguments": {"until": "precombat_main"}},
            {"name": "choose_action", "arguments": {"index": 0}},
            # End turn, wait for opponent to cast Savannah Lions.
            {"name": "pass_priority", "arguments": {"until": "end_of_turn"}},
            # Counter Savannah Lions with Mana Drain.
            {"name": "choose_action", "arguments": {"index": 0}},
            # Skip to our next precombat main (Mana Drain mana available).
            {"name": "pass_priority", "arguments": {"until": "my_turn"}},
            {"name": "pass_priority", "arguments": {"until": "precombat_main"}},
            # Cast Fact or Fiction using Mana Drain mana.
            {"name": "choose_action", "arguments": {"index": 0, "mana_plan": ["COLORLESS"]}},
            # Choose the 3-card pile.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"pile": 1}},
            {"name": "get_game_state", "arguments": {}},
        ],
        script_b=[
            # Keep opening hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # Turn 1: play Plains.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {"until": "end_of_turn"}},
            # Turn 2: cast Savannah Lions (only playable card).
            {"name": "pass_priority", "arguments": {"until": "precombat_main"}},
            {"name": "choose_action", "arguments": {"index": 0}},
            # Split piles 3/2 for Fact or Fiction (pick three cards for pile 1, then done).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # Stay alive until game ends.
            {"name": "pass_priority", "arguments": {}},
        ],
    ),
    # --- Savannah Lions trade in combat ---
    Scenario(
        golden_name="savannah_lions_trade",
        deck_a=DECK_SAVANNAH_LIONS,
        deck_b=DECK_SAVANNAH_LIONS,
        player_a_name="Player_SL",
        player_b_name="Opp_SL",
        spectator_name="spec_sl",
        script_a=[
            # Choose starting player, keep hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # T1: Play Plains (first land choice).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            # T1: Cast Savannah Lions immediately (only castable spell after land).
            {"name": "choose_action", "arguments": {"index": 0}},
            # T2: Precombat main — skip land play.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # T2: Declare attackers — attack with Savannah Lions.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"attackers": ["all"]}},
            # Mid-game history check: should show land plays, casts, and attack.
            {"name": "get_game_history", "arguments": {}},
            # Pass through combat (P2 blocks) to postcombat main.
            {"name": "pass_priority", "arguments": {"until": "postcombat_main"}},
            # Capture final state: both Lions should be in graveyards.
            {"name": "get_game_state", "arguments": {}},
        ],
        script_b=[
            # Keep opening hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # P2's T1: Play Plains.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            # P2's T1: Cast Savannah Lions (chained, no pass_priority).
            {"name": "choose_action", "arguments": {"index": 0}},
            # Wait for P1's T2 attack -> declare blockers.
            {"name": "pass_priority", "arguments": {}},
            # Declare lone Savannah Lions blocker against lone attacker using indexes.
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "choose_action", "arguments": {"answer": True}},
            # Stay alive until P1's script finishes.
            {"name": "pass_priority", "arguments": {}},
        ],
    ),
    # --- Lightning Bolt with stack_resolved yield ---
    Scenario(
        golden_name="stack_resolved",
        deck_a=DECK_BOLT_AND_BURN,
        deck_b=DECK_FILLER,
        player_a_name="Player_SR",
        player_b_name="Opp_SR",
        spectator_name="spec_sr",
        script_a=[
            # Choose starting player, keep hand.
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"answer": False}},
            # T1: Play Mountain (hand alphabetical: Badlands=p10, LB=p11, LB=p12,
            # Memnite=p13, Mountain=p14, Plateau=p15, Taiga=p16).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"id": "p14"}},
            # Cast Lightning Bolt #1 (first playable spell).
            {"name": "pass_priority", "arguments": {}},
            {"name": "choose_action", "arguments": {"index": 0}},
            # Target Opponent.
            {"name": "choose_action", "arguments": {"id": "p2"}},
            # Let the stack resolve — bolt deals 3 damage.
            {"name": "pass_priority", "arguments": {"until": "stack_resolved"}},
            {"name": "get_game_state", "arguments": {}},
        ],
        script_b=None,  # Uses potato opponent via run_golden_scenario
    ),
]


def get_scenario(golden_name: str) -> Scenario:
    """Look up a scenario by golden_name."""
    for s in SUBPROCESS_SCENARIOS:
        if s.golden_name == golden_name:
            return s
    raise ValueError(f"Unknown scenario: {golden_name}")
