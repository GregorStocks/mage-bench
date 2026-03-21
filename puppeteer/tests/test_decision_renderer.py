"""Tests for the shared decision renderer."""

import pytest

from puppeteer.decision_renderer import (
    _batch_attack_display,
    _batch_block_display,
    _chosen_display,
    _format_choice,
    _render_card_reference,
    _render_chosen_block,
    _resolve_mana_plan,
    card_display,
    permanent_display,
    render_decision,
)
from schemas.game_export_types import (
    Choice,
    Decision,
    MultiAmountItem,
    Permanent,
    PilotContext,
    Snapshot,
    StackTarget,
    require_snapshot,
)


def _snapshot_player(player: dict[str, object], *, index: int) -> dict[str, object]:
    default_name = "Alice" if index == 0 else "Bob" if index == 1 else f"Player {index + 1}"
    result: dict[str, object] = {
        "name": default_name,
        "life": 20,
        "library_size": 50,
        "battlefield": [],
        "graveyard": [],
        "hand": [],
        "hand_count": 0,
        "exile": [],
    }
    result.update(player)
    if "hand_count" not in player:
        hand = result["hand"]
        assert isinstance(hand, list), f"snapshot player hand must be a list, got {hand!r}"
        result["hand_count"] = len(hand)
    return result


def _make_snapshot(
    *,
    turn: int = 3,
    phase: str = "PRECOMBAT_MAIN",
    step: str | None = "PRECOMBAT_MAIN",
    players: list[dict] | None = None,
    stack: list | None = None,
    combat: list | None = None,
) -> Snapshot:
    if players is None:
        players = [
            {
                "name": "Alice",
                "life": 20,
                "library_size": 50,
                "battlefield": [{"name": "Mountain"}],
                "graveyard": [],
                "hand": [{"name": "Lightning Bolt"}, {"name": "Mountain"}],
                "hand_count": 2,
                "exile": [],
            },
            {
                "name": "Bob",
                "life": 18,
                "library_size": 52,
                "battlefield": [{"name": "Island"}],
                "graveyard": [],
                "hand": [{"name": "Counterspell"}, {"name": "Island"}],
                "hand_count": 2,
                "exile": [],
            },
        ]
    return require_snapshot(
        {
            "seq": 100,
            "turn": turn,
            "phase": phase,
            "step": step,
            "active_player": "Alice",
            "priority_player": "Alice",
            "players": [_snapshot_player(player, index=index) for index, player in enumerate(players)],
            "stack": stack or [],
            "combat": combat or [],
        },
        source="decision renderer test snapshot",
    )


def _make_decision(
    *,
    index: int = 0,
    snapshot_index: int = 5,
    player: str = "Alice",
    turn: int | None = 3,
    phase: str | None = "PRECOMBAT_MAIN",
    step: str | None = "PRECOMBAT_MAIN",
    action_type: str = "GAME_SELECT",
    response_type: str = "select",
    message: str = "Play spells and abilities",
    choices: list | None = None,
    items: list | None = None,
    total_min: int | None = None,
    total_max: int | None = None,
    pilot_context: PilotContext | None = None,
    chosen: object = 0,
    chosen_args: dict | None = None,
    llm_event_indices: list[int] | None = None,
    subsequent_actions: list[str] | None = None,
) -> Decision:
    if choices is None and items is None:
        choices = [
            Choice.from_mapping(
                {"index": 0, "name": "Lightning Bolt", "id": "p3", "action": "cast", "mana_cost": "{R}"}
            ),
            Choice.from_mapping({"index": 1, "name": "Mountain", "id": "p5", "action": "land"}),
        ]
    if choices is None:
        choices = []
    # Convert any remaining raw dicts to Choice dataclasses
    choices = Choice.coerce_list(choices)
    d: dict[str, object] = {
        "index": index,
        "snapshotIndex": snapshot_index,
        "player": player,
        "turn": turn,
        "phase": phase,
        "step": step,
        "actionType": action_type,
        "responseType": response_type,
        "message": message,
        "choices": choices,
        "choiceCount": len(choices),
        "isForced": len(choices) <= 1,
        "chosen": chosen,
        "chosenArgs": chosen_args or {},
        "actionResult": {"success": True, "action_taken": "selected_0"},
        "llmEventIndices": llm_event_indices or [10, 11, 12],
        "subsequentActions": subsequent_actions or [],
    }
    if items is not None:
        # Convert any remaining raw dicts to MultiAmountItem dataclasses
        d["items"] = MultiAmountItem.coerce_list(items)
    if total_min is not None:
        d["totalMin"] = total_min
    if total_max is not None:
        d["totalMax"] = total_max
    if pilot_context is not None:
        d["pilotContext"] = pilot_context
    return Decision.from_dict(d)


class TestRenderDecision:
    def test_basic_render(self) -> None:
        snap = _make_snapshot()
        decision = _make_decision()
        text = render_decision(decision, snap)
        assert "[Decision 0, snapshot=5]" in text
        assert "Turn 3 PRECOMBAT_MAIN - Alice" in text
        assert "Alice: 20hp" in text
        assert "Bob: 18hp" in text
        assert "Lightning Bolt" in text
        assert "Mountain" in text

    def test_hand_redaction(self) -> None:
        snap = _make_snapshot()
        decision = _make_decision()
        text = render_decision(decision, snap, deciding_player="Alice")
        # Alice's hand is shown
        assert "hand=[Lightning Bolt, Mountain]" in text
        # Bob's hand is redacted to count
        assert "Bob: 18hp hand=2" in text
        assert "Counterspell" not in text

    def test_no_redaction_without_deciding_player(self) -> None:
        snap = _make_snapshot()
        decision = _make_decision()
        text = render_decision(decision, snap)
        # Both hands visible
        assert "Lightning Bolt" in text
        assert "Counterspell" in text

    def test_pilot_context(self) -> None:
        snap = _make_snapshot()
        decision = _make_decision(pilot_context=PilotContext.from_mapping({"untappedLands": 2, "landDropsUsed": 0}))
        text = render_decision(decision, snap)
        assert "Untapped lands: 2" in text
        assert "Land drops remaining: 1" in text

    def test_pilot_context_dataclass(self) -> None:
        snap = _make_snapshot()
        decision = _make_decision(
            pilot_context=PilotContext.from_mapping({"untappedLands": 2, "landDropsUsed": 0, "combatPhase": None})
        )
        text = render_decision(decision, snap)
        assert "Untapped lands: 2" in text
        assert "Land drops remaining: 1" in text

    def test_pilot_context_must_be_object(self) -> None:
        snap = _make_snapshot()
        decision = _make_decision()
        decision.pilot_context = "bad"  # type: ignore[assignment]

        with pytest.raises(AssertionError, match="pilotContext must be an object"):
            render_decision(decision, snap)

    def test_stack_rendering(self) -> None:
        snap = _make_snapshot(stack=[{"name": "Lightning Bolt", "targets": ["Bob"]}])
        decision = _make_decision()
        text = render_decision(decision, snap)
        assert "Stack: [Lightning Bolt -> Bob]" in text

    def test_stack_rendering_allows_empty_stack_item_name(self) -> None:
        snap = _make_snapshot(stack=[{"name": "", "targets": ["Bob"]}])
        decision = _make_decision()
        text = render_decision(decision, snap)
        assert "Stack: [ -> Bob]" in text

    def test_triggered_ability_stack_rendering(self) -> None:
        snap = _make_snapshot(
            stack=[
                {
                    "name": "Ability",
                    "source_card": "Emancipation Angel",
                    "ability_text": (
                        "When Emancipation Angel enters, return a permanent you control to its owner's hand."
                    ),
                    "targets": [{"name": "Alice (you)", "id": "p2"}],
                }
            ]
        )
        decision = _make_decision()
        text = render_decision(decision, snap)
        assert (
            "Stack: [Emancipation Angel - When Emancipation Angel enters, return a permanent you control "
            "to its owner's hand. -> Alice (you)]"
        ) in text

    def test_combat_rendering(self) -> None:
        snap = _make_snapshot(
            combat=[
                {
                    "attackers": [{"name": "Goblin Guide"}],
                    "blockers": [],
                    "blocked": False,
                    "defending": "Bob",
                }
            ]
        )
        decision = _make_decision()
        text = render_decision(decision, snap)
        assert "Combat: Goblin Guide -> Bob" in text

    @pytest.mark.parametrize("combat_phase", ["blockers", "declare_blockers"])
    def test_blockers_prompt_renders_incoming_attacker_ids(self, combat_phase: str) -> None:
        snap = _make_snapshot(
            combat=[
                {
                    "attackers": [{"name": "Goblin Token"}, {"name": "Goblin Token"}],
                    "blockers": [],
                    "blocked": False,
                    "defending": "Alice",
                }
            ]
        )
        decision = _make_decision(
            phase="COMBAT",
            message="Select blockers",
            choices=[Choice.from_mapping({"index": 0, "name": "Wall of Omens", "id": "p30", "choice_type": "blocker"})],
            pilot_context=PilotContext.from_mapping(
                {
                    "combatPhase": combat_phase,
                    "incomingAttackers": [
                        {"name": "Goblin Token", "id": "p10", "power": "1", "toughness": "1"},
                        {"name": "Goblin Token", "id": "p11", "power": "1", "toughness": "1"},
                    ],
                }
            ),
        )
        text = render_decision(decision, snap)
        assert "Incoming Attackers: Goblin Token [id=p10, 1/1], Goblin Token [id=p11, 1/1]" in text

    def test_include_chosen(self) -> None:
        snap = _make_snapshot()
        decision = _make_decision()
        text = render_decision(
            decision,
            snap,
            include_chosen=True,
        )
        assert "Chosen: Lightning Bolt" in text
        # Reasoning and After sections are no longer included
        assert "Reasoning:" not in text
        assert "After:" not in text

    def test_card_reference(self) -> None:
        snap = _make_snapshot()
        decision = _make_decision()
        oracle_texts = {
            "Lightning Bolt": {
                "mana_cost": "{R}",
                "type_line": "Instant",
                "oracle_text": "Lightning Bolt deals 3 damage to any target.",
            },
            "Mountain": {},  # basic, should be filtered
        }
        text = render_decision(
            decision,
            snap,
            oracle_texts=oracle_texts,
            include_card_reference=True,
        )
        assert "## Card Reference" in text
        assert "Lightning Bolt {R} -- Instant" in text
        assert "3 damage" in text
        # Mountain is a basic land, should not appear in card reference
        card_ref_section = text.split("## Card Reference")[1].split("\n\n")[0]
        assert "Mountain" not in card_ref_section

    def test_prior_context_and_turn_actions(self) -> None:
        snap = _make_snapshot()
        decision = _make_decision()
        text = render_decision(
            decision,
            snap,
            prior_context="## Prior Context\nSome prior stuff",
            current_turn_actions="## This Turn\nAlice plays Mountain",
        )
        assert "## Prior Context" in text
        assert "Some prior stuff" in text
        assert "## This Turn" in text
        assert "Alice plays Mountain" in text

    def test_triggered_ability_note(self) -> None:
        snap = _make_snapshot()
        decision = _make_decision(
            message="Pick triggered ability (triggers)",
        )
        text = render_decision(decision, snap)
        assert "NOTE: This decision only determines the order" in text

    def test_cast_rolled_back(self) -> None:
        snap = _make_snapshot()
        decision = _make_decision()
        decision.cast_rolled_back = True
        text = render_decision(
            decision,
            snap,
            include_chosen=True,
        )
        assert "rolled it back" in text.lower() or "rolled back" in text.lower()

    def test_decision_heading(self) -> None:
        snap = _make_snapshot()
        decision = _make_decision()
        text = render_decision(decision, snap)
        assert "## Decision" in text

    def test_pregame_phase(self) -> None:
        snap = _make_snapshot(turn=0, phase="?")
        decision = _make_decision(turn=0, phase=None)
        text = render_decision(decision, snap)
        assert "Turn 0 PREGAME" in text

    def test_mulligan_phase_turn_1_empty_string(self) -> None:
        """Mulligan decisions have turn=1 and phase=''. Should render as PREGAME."""
        snap = _make_snapshot(turn=1, phase="")
        decision = _make_decision(turn=1, phase="")
        text = render_decision(decision, snap)
        assert "Turn 1 PREGAME" in text

    def test_turn_none_crashes(self) -> None:
        """Decision turn is schema-required and should fail fast when missing."""
        with pytest.raises(AssertionError, match=r"Decision\.turn: expected int"):
            _make_decision(turn=None, phase=None)

    def test_empty_phase_after_turn_1_crashes(self) -> None:
        """Empty phase on turn > 1 indicates data corruption and should crash."""
        snap = _make_snapshot(turn=5, phase="")
        decision = _make_decision(turn=5, phase="")
        with pytest.raises(AssertionError, match="empty phase on turn 5"):
            render_decision(decision, snap)


_ITEMS_FIXTURE = [
    {"description": "Savannah Lions, P/T: 2/1", "min": 0, "max": 2},
    {"description": "Grizzly Bears, P/T: 2/2", "min": 0, "max": 2},
]


class TestItemsRendering:
    """Tests for multi-amount Items header rendering."""

    def test_equal_totals_simplified(self) -> None:
        snap = _make_snapshot()
        decision = _make_decision(items=_ITEMS_FIXTURE, total_min=2, total_max=2)
        text = render_decision(decision, snap)
        assert "Items (2): total=2" in text
        assert "total_min" not in text
        assert "total_max" not in text

    def test_unequal_totals_show_both(self) -> None:
        snap = _make_snapshot()
        decision = _make_decision(items=_ITEMS_FIXTURE, total_min=0, total_max=3)
        text = render_decision(decision, snap)
        assert "Items (2): total_min=0, total_max=3" in text

    def test_only_total_min_set(self) -> None:
        snap = _make_snapshot()
        decision = _make_decision(items=_ITEMS_FIXTURE, total_min=2)
        text = render_decision(decision, snap)
        assert "Items (2): total_min=2" in text

    def test_only_total_max_set(self) -> None:
        snap = _make_snapshot()
        decision = _make_decision(items=_ITEMS_FIXTURE, total_max=5)
        text = render_decision(decision, snap)
        assert "Items (2): total_max=5" in text

    def test_no_totals_set(self) -> None:
        snap = _make_snapshot()
        decision = _make_decision(items=_ITEMS_FIXTURE)
        text = render_decision(decision, snap)
        assert "Items (2)\n" in text or text.endswith("Items (2)")


class TestFormatChoice:
    def test_simple_string(self) -> None:
        assert _format_choice("Yes") == "Yes"

    def test_choice_with_name(self) -> None:
        c = Choice.from_mapping({"name": "Mountain", "id": "p5", "action": "land"})
        assert _format_choice(c) == "Mountain [id=p5, land]"

    def test_choice_with_mana_cost(self) -> None:
        c = Choice.from_mapping({"name": "Lightning Bolt", "id": "p3", "action": "cast", "mana_cost": "{R}"})
        result = _format_choice(c)
        assert "Lightning Bolt" in result
        assert "{R}" in result

    def test_dataclass_choice(self) -> None:
        result = _format_choice(Choice.from_mapping({"name": "Lightning Bolt", "id": "p3", "action": "cast"}))
        assert result == "Lightning Bolt [id=p3, cast]"


class TestPermanentDisplay:
    def test_simple_name(self) -> None:
        assert permanent_display(Permanent(name="Island")) == "Island"

    def test_tapped(self) -> None:
        assert permanent_display(Permanent(name="Mountain", tapped=True)) == "Mountain (tapped)"

    def test_counters(self) -> None:
        result = permanent_display(Permanent(name="Thalia", counters=[{"name": "+1/+1", "count": 2}]))
        assert "Thalia" in result
        assert "+1/+1=2" in result

    def test_power_toughness(self) -> None:
        assert permanent_display(Permanent(name="Goblin Guide", pt="2/2")) == "Goblin Guide 2/2"

    def test_string_input(self) -> None:
        assert permanent_display("Island") == "Island"

    def test_loyalty(self) -> None:
        assert permanent_display(Permanent(name="Karn", loyalty=5)) == "Karn (loyalty=5)"

    def test_token(self) -> None:
        assert permanent_display(Permanent(name="Soldier", token=True)) == "Soldier (token)"

    def test_copy_of_original(self) -> None:
        result = permanent_display(Permanent(name="Phyrexian Metamorph", original_card="Sol Ring"))
        assert result == "Phyrexian Metamorph (copy of Sol Ring)"

    def test_copy_without_original(self) -> None:
        result = permanent_display(Permanent(name="Clone", copy=True))
        assert result == "Clone (copy)"

    def test_power_toughness_integers(self) -> None:
        result = permanent_display(Permanent(name="Bear", power="2", toughness="2"))
        assert result == "Bear 2/2"

    def test_multiple_extras(self) -> None:
        result = permanent_display(
            Permanent(
                name="Soldier",
                tapped=True,
                token=True,
                counters=[{"name": "+1/+1", "count": 1}],
            )
        )
        assert "tapped" in result
        assert "token" in result
        assert "+1/+1=1" in result


class TestCardReference:
    def test_filters_basics(self) -> None:
        snap = _make_snapshot()
        decision = _make_decision()
        oracle_texts = {
            "Mountain": {"type_line": "Basic Land — Mountain"},
            "Lightning Bolt": {"mana_cost": "{R}", "type_line": "Instant", "oracle_text": "Deal 3."},
        }
        ref = _render_card_reference(decision, snap, oracle_texts)
        assert "Lightning Bolt" in ref
        # Mountain is basic, should not appear in reference
        lines = ref.split("\n")
        mountain_lines = [line for line in lines if line.startswith("- Mountain")]
        assert len(mountain_lines) == 0

    def test_empty_oracle_texts(self) -> None:
        snap = _make_snapshot()
        decision = _make_decision()
        assert _render_card_reference(decision, snap, {}) == ""


def _choice_list(*specs: dict) -> list[Choice]:
    """Helper to create a list of Choice dataclasses from dicts."""
    return [Choice.from_mapping(s) for s in specs]


class TestBatchAttackDisplay:
    def test_list_format(self) -> None:
        choices = _choice_list({"name": "Bear", "id": "p1"}, {"name": "Elf", "id": "p2"})
        assert _batch_attack_display(["p1", "p2"], choices) == "Attack with Bear, Elf"

    def test_string_format(self) -> None:
        """Comma-separated string format (epoch 36+)."""
        choices = _choice_list({"name": "Bear", "id": "p1"}, {"name": "Elf", "id": "p2"})
        assert _batch_attack_display("p1,p2", choices) == "Attack with Bear, Elf"

    def test_string_all(self) -> None:
        """String 'all' format (epoch 36+)."""
        choices = _choice_list({"name": "Bear", "id": "p1"}, {"name": "Elf", "id": "p2"})
        assert _batch_attack_display("all", choices) == "Attack with all (Bear, Elf)"

    def test_list_all(self) -> None:
        choices = _choice_list({"name": "Bear", "id": "p1"}, {"name": "Elf", "id": "p2"})
        assert _batch_attack_display(["all"], choices) == "Attack with all (Bear, Elf)"


class TestBatchBlockDisplay:
    def test_list_format(self) -> None:
        choices = _choice_list(
            {"name": "Bear", "id": "p1"},
            {"name": "Elf", "id": "p2"},
            {"name": "Goblin", "id": "p3"},
        )
        result = _batch_block_display(["p1:p3"], choices)
        assert result == "Bear blocks Goblin"

    def test_string_format(self) -> None:
        """Comma-separated string format (epoch 36+)."""
        choices = _choice_list(
            {"name": "Bear", "id": "p1"},
            {"name": "Elf", "id": "p2"},
            {"name": "Goblin", "id": "p3"},
        )
        result = _batch_block_display("p1:p3,p2:p3", choices)
        assert result == "Bear blocks Goblin, Elf blocks Goblin"


class TestChosenDisplay:
    def test_batch_attackers_string(self) -> None:
        """String-format attackers in chosen_args (epoch 36+)."""
        choices = _choice_list({"name": "Bear", "id": "p1"}, {"name": "Elf", "id": "p2"})
        result = _chosen_display(None, {"attackers": "p1,p2"}, choices)
        assert result == "Attack with Bear, Elf"

    def test_batch_blockers_string(self) -> None:
        """String-format blockers in chosen_args (epoch 36+)."""
        choices = _choice_list({"name": "Bear", "id": "p1"}, {"name": "Goblin", "id": "p3"})
        result = _chosen_display(None, {"blockers": "p1:p3"}, choices)
        assert result == "Bear blocks Goblin"

    def test_boolean_chosen(self) -> None:
        true_value = True
        false_value = False
        assert _chosen_display(true_value, {}, []) == "True"
        assert _chosen_display(false_value, {}, []) == "False"

    def test_index_chosen(self) -> None:
        choices = _choice_list({"name": "Lightning Bolt"}, {"name": "Mountain"})
        assert _chosen_display(0, {}, choices) == "Lightning Bolt"

    def test_index_chosen_dataclass(self) -> None:
        choices = [Choice(name="Lightning Bolt"), Choice(name="Mountain")]
        assert _chosen_display(0, {}, choices) == "Lightning Bolt"

    def test_text_chosen(self) -> None:
        """Text-based choices (e.g. color picking) show the text value."""
        result = _chosen_display(None, {"text": "Green"}, [])
        assert result == "Text: Green"

    def test_no_response(self) -> None:
        """chosen=None with empty chosen_args returns (no response)."""
        assert _chosen_display(None, {}, []) == "(no response)"
        assert _chosen_display(None, None, []) == "(no response)"


class TestResolveManaplan:
    def test_resolves_ids_to_names(self) -> None:
        snapshot = _make_snapshot(
            players=[{"battlefield": [{"name": "Mountain", "id": "p1"}, {"name": "Forest", "id": "p5"}]}]
        )
        assert _resolve_mana_plan("p1,p5", snapshot) == "Mountain (p1), Forest (p5)"

    def test_multi_ability_land_selector(self) -> None:
        snapshot = _make_snapshot(players=[{"battlefield": [{"name": "Stomping Ground", "id": "p5"}]}])
        assert _resolve_mana_plan("p5:1", snapshot) == "Stomping Ground (p5:1)"

    def test_pool_colors_unresolved(self) -> None:
        snapshot = _make_snapshot(players=[{"battlefield": []}])
        assert _resolve_mana_plan("RED,BLUE", snapshot) == "RED, BLUE"

    def test_no_snapshot(self) -> None:
        assert _resolve_mana_plan("p1,p5:1", None) == "p1, p5:1"

    def test_mixed_ids_and_colors(self) -> None:
        snapshot = _make_snapshot(players=[{"battlefield": [{"name": "Mountain", "id": "p1"}]}])
        assert _resolve_mana_plan("p1,RED", snapshot) == "Mountain (p1), RED"

    def test_list_form_resolves_ids_to_names(self) -> None:
        snapshot = _make_snapshot(
            players=[{"battlefield": [{"name": "Mountain", "id": "p1"}, {"name": "Forest", "id": "p5"}]}]
        )
        assert _resolve_mana_plan(["p1", "p5:1"], snapshot) == "Mountain (p1), Forest (p5:1)"

    def test_structured_entries_resolve_tap_and_pool(self) -> None:
        snapshot = _make_snapshot(players=[{"battlefield": [{"name": "Mountain", "id": "p1"}]}])
        mana_plan = [{"tap": "p1"}, {"pool": "RED"}]
        assert _resolve_mana_plan(mana_plan, snapshot) == "Mountain (p1), RED"


class TestChosenBlockManaPlan:
    def test_shows_mana_plan(self) -> None:
        decision = _make_decision(
            chosen=0,
            chosen_args={"choice": "p3", "mana_plan": "p1,p5"},
            choices=_choice_list({"name": "Lightning Bolt", "id": "p3"}),
            subsequent_actions=[],
        )
        snapshot = _make_snapshot(
            players=[{"battlefield": [{"name": "Mountain", "id": "p1"}, {"name": "Forest", "id": "p5"}]}]
        )
        block = _render_chosen_block(decision, snapshot)
        assert "Chosen: Lightning Bolt" in block
        assert "Mana plan: Mountain (p1), Forest (p5)" in block

    def test_shows_list_form_mana_plan(self) -> None:
        decision = _make_decision(
            chosen=0,
            chosen_args={"choice": "p3", "mana_plan": ["p1", "p5:1"]},
            choices=_choice_list({"name": "Lightning Bolt", "id": "p3"}),
            subsequent_actions=[],
        )
        snapshot = _make_snapshot(
            players=[{"battlefield": [{"name": "Mountain", "id": "p1"}, {"name": "Forest", "id": "p5"}]}]
        )
        block = _render_chosen_block(decision, snapshot)
        assert "Mana plan: Mountain (p1), Forest (p5:1)" in block

    def test_shows_structured_mana_plan(self) -> None:
        decision = _make_decision(
            chosen=0,
            chosen_args={"choice": "p3", "mana_plan": [{"tap": "p1"}, {"pool": "RED"}]},
            choices=_choice_list({"name": "Lightning Bolt", "id": "p3"}),
            subsequent_actions=[],
        )
        snapshot = _make_snapshot(players=[{"battlefield": [{"name": "Mountain", "id": "p1"}]}])
        block = _render_chosen_block(decision, snapshot)
        assert "Mana plan: Mountain (p1), RED" in block

    def test_shows_auto_tap_false(self) -> None:
        decision = _make_decision(
            chosen=0,
            chosen_args={"choice": "p3", "mana_plan": "p1", "auto_tap": False},
            choices=_choice_list({"name": "Lightning Bolt", "id": "p3"}),
            subsequent_actions=[],
        )
        snapshot = _make_snapshot(players=[{"battlefield": [{"name": "Mountain", "id": "p1"}]}])
        block = _render_chosen_block(decision, snapshot)
        assert "auto_tap=false" in block

    def test_no_mana_plan_no_extra_line(self) -> None:
        decision = _make_decision(
            chosen=0,
            chosen_args={"choice": "p3"},
            choices=_choice_list({"name": "Lightning Bolt", "id": "p3"}),
            subsequent_actions=[],
        )
        block = _render_chosen_block(decision)
        assert "Mana plan" not in block

    def test_chosen_args_must_be_object(self) -> None:
        decision = _make_decision(
            chosen=0,
            choices=_choice_list({"name": "Lightning Bolt", "id": "p3"}),
            subsequent_actions=[],
        )
        decision.chosen_args = "bad"  # type: ignore[assignment]

        with pytest.raises(AssertionError, match="chosenArgs must be an object"):
            _render_chosen_block(decision)

    def test_mana_plan_in_full_render(self) -> None:
        snap = _make_snapshot(
            players=[
                {
                    "name": "Alice",
                    "life": 20,
                    "library_size": 50,
                    "battlefield": [{"name": "Mountain", "id": "p1"}],
                    "graveyard": [],
                    "hand": [{"name": "Lightning Bolt"}],
                    "hand_count": 1,
                    "exile": [],
                },
            ]
        )
        decision = _make_decision(
            chosen=0,
            chosen_args={"choice": "p3", "mana_plan": "p1"},
        )
        text = render_decision(decision, snap, include_chosen=True)
        assert "Mana plan: Mountain (p1)" in text


class TestFailFastOnMissingName:
    """Required 'name' field must crash, not fall back to '?'."""

    def test_card_display_crashes_on_missing_name(self) -> None:
        with pytest.raises(AssertionError, match="card name must be a string"):
            card_display(StackTarget(id="p1"))

    def test_permanent_display_crashes_on_missing_name(self) -> None:
        with pytest.raises(AssertionError, match="permanent name must be a string"):
            permanent_display(StackTarget(id="p1"))
