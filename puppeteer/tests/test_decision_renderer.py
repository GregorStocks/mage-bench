"""Tests for the shared decision renderer."""

from puppeteer.decision_renderer import (
    _batch_attack_display,
    _batch_block_display,
    _chosen_display,
    _format_choice,
    _render_card_reference,
    _render_chosen_block,
    _resolve_mana_plan,
    permanent_display,
    render_decision,
)


def _make_snapshot(
    *,
    turn: int = 3,
    phase: str = "PRECOMBAT_MAIN",
    step: str | None = "PRECOMBAT_MAIN",
    players: list[dict] | None = None,
    stack: list | None = None,
    combat: list | None = None,
) -> dict:
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
    return {
        "seq": 100,
        "turn": turn,
        "phase": phase,
        "step": step,
        "active_player": "Alice",
        "priority_player": "Alice",
        "players": players,
        "stack": stack or [],
        "combat": combat or [],
    }


def _make_decision(
    *,
    index: int = 0,
    snapshot_index: int = 5,
    player: str = "Alice",
    turn: int = 3,
    phase: str = "PRECOMBAT_MAIN",
    message: str = "Play spells and abilities",
    choices: list | None = None,
    pilot_context: dict | None = None,
    chosen: object = 0,
    chosen_args: dict | None = None,
    llm_event_indices: list[int] | None = None,
    subsequent_actions: list[str] | None = None,
) -> dict:
    if choices is None:
        choices = [
            {"index": 0, "name": "Lightning Bolt", "id": "p3", "action": "cast", "mana_cost": "{R}"},
            {"index": 1, "name": "Mountain", "id": "p5", "action": "land"},
        ]
    d: dict = {
        "index": index,
        "snapshotIndex": snapshot_index,
        "player": player,
        "turn": turn,
        "phase": phase,
        "step": "PRECOMBAT_MAIN",
        "actionType": "GAME_SELECT",
        "responseType": "select",
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
    if pilot_context is not None:
        d["pilotContext"] = pilot_context
    return d


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
        decision = _make_decision(pilot_context={"untappedLands": 2, "landDropsUsed": 0})
        text = render_decision(decision, snap)
        assert "Untapped lands: 2" in text
        assert "Land drops remaining: 1" in text

    def test_stack_rendering(self) -> None:
        snap = _make_snapshot(stack=[{"name": "Lightning Bolt", "targets": ["Bob"]}])
        decision = _make_decision()
        text = render_decision(decision, snap)
        assert "Stack: [Lightning Bolt -> Bob]" in text

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
        decision["castRolledBack"] = True
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


class TestFormatChoice:
    def test_simple_string(self) -> None:
        assert _format_choice("Yes") == "Yes"

    def test_dict_with_name(self) -> None:
        assert _format_choice({"name": "Mountain", "id": "p5", "action": "land"}) == "Mountain [id=p5, land]"

    def test_dict_with_mana_cost(self) -> None:
        result = _format_choice({"name": "Lightning Bolt", "id": "p3", "action": "cast", "mana_cost": "{R}"})
        assert "Lightning Bolt" in result
        assert "{R}" in result


class TestPermanentDisplay:
    def test_simple_name(self) -> None:
        assert permanent_display({"name": "Island"}) == "Island"

    def test_tapped(self) -> None:
        assert permanent_display({"name": "Mountain", "tapped": True}) == "Mountain (tapped)"

    def test_counters(self) -> None:
        result = permanent_display({"name": "Thalia", "counters": [{"name": "+1/+1", "count": 2}]})
        assert "Thalia" in result
        assert "+1/+1=2" in result

    def test_power_toughness(self) -> None:
        assert permanent_display({"name": "Goblin Guide", "pt": "2/2"}) == "Goblin Guide 2/2"

    def test_string_input(self) -> None:
        assert permanent_display("Island") == "Island"

    def test_loyalty(self) -> None:
        assert permanent_display({"name": "Karn", "loyalty": 5}) == "Karn (loyalty=5)"

    def test_token(self) -> None:
        assert permanent_display({"name": "Soldier", "token": True}) == "Soldier (token)"

    def test_copy_of_original(self) -> None:
        result = permanent_display({"name": "Phyrexian Metamorph", "original_card": "Sol Ring"})
        assert result == "Phyrexian Metamorph (copy of Sol Ring)"

    def test_copy_without_original(self) -> None:
        result = permanent_display({"name": "Clone", "copy": True})
        assert result == "Clone (copy)"

    def test_power_toughness_integers(self) -> None:
        result = permanent_display({"name": "Bear", "power": "2", "toughness": "2"})
        assert result == "Bear 2/2"

    def test_multiple_extras(self) -> None:
        result = permanent_display(
            {
                "name": "Soldier",
                "tapped": True,
                "token": True,
                "counters": [{"name": "+1/+1", "count": 1}],
            }
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


class TestBatchAttackDisplay:
    def test_list_format(self) -> None:
        choices = [{"name": "Bear", "id": "p1"}, {"name": "Elf", "id": "p2"}]
        assert _batch_attack_display(["p1", "p2"], choices) == "Attack with Bear, Elf"

    def test_string_format(self) -> None:
        """Comma-separated string format (epoch 36+)."""
        choices = [{"name": "Bear", "id": "p1"}, {"name": "Elf", "id": "p2"}]
        assert _batch_attack_display("p1,p2", choices) == "Attack with Bear, Elf"

    def test_string_all(self) -> None:
        """String 'all' format (epoch 36+)."""
        choices = [{"name": "Bear", "id": "p1"}, {"name": "Elf", "id": "p2"}]
        assert _batch_attack_display("all", choices) == "Attack with all (Bear, Elf)"

    def test_list_all(self) -> None:
        choices = [{"name": "Bear", "id": "p1"}, {"name": "Elf", "id": "p2"}]
        assert _batch_attack_display(["all"], choices) == "Attack with all (Bear, Elf)"


class TestBatchBlockDisplay:
    def test_list_format(self) -> None:
        choices = [
            {"name": "Bear", "id": "p1"},
            {"name": "Elf", "id": "p2"},
            {"name": "Goblin", "id": "p3"},
        ]
        result = _batch_block_display(["p1:p3"], choices)
        assert result == "Bear blocks Goblin"

    def test_string_format(self) -> None:
        """Comma-separated string format (epoch 36+)."""
        choices = [
            {"name": "Bear", "id": "p1"},
            {"name": "Elf", "id": "p2"},
            {"name": "Goblin", "id": "p3"},
        ]
        result = _batch_block_display("p1:p3,p2:p3", choices)
        assert result == "Bear blocks Goblin, Elf blocks Goblin"


class TestChosenDisplay:
    def test_batch_attackers_string(self) -> None:
        """String-format attackers in chosen_args (epoch 36+)."""
        choices = [{"name": "Bear", "id": "p1"}, {"name": "Elf", "id": "p2"}]
        result = _chosen_display(None, {"attackers": "p1,p2"}, choices)
        assert result == "Attack with Bear, Elf"

    def test_batch_blockers_string(self) -> None:
        """String-format blockers in chosen_args (epoch 36+)."""
        choices = [{"name": "Bear", "id": "p1"}, {"name": "Goblin", "id": "p3"}]
        result = _chosen_display(None, {"blockers": "p1:p3"}, choices)
        assert result == "Bear blocks Goblin"

    def test_boolean_chosen(self) -> None:
        assert _chosen_display(True, {}, []) == "True"
        assert _chosen_display(False, {}, []) == "False"

    def test_index_chosen(self) -> None:
        choices = [{"name": "Lightning Bolt"}, {"name": "Mountain"}]
        assert _chosen_display(0, {}, choices) == "Lightning Bolt"


class TestResolveManaplan:
    def test_resolves_ids_to_names(self) -> None:
        snapshot = {
            "players": [
                {"battlefield": [{"name": "Mountain", "id": "p1"}, {"name": "Forest", "id": "p5"}]},
            ]
        }
        assert _resolve_mana_plan("p1,p5", snapshot) == "Mountain (p1), Forest (p5)"

    def test_multi_ability_land_selector(self) -> None:
        snapshot = {
            "players": [
                {"battlefield": [{"name": "Stomping Ground", "id": "p5"}]},
            ]
        }
        assert _resolve_mana_plan("p5:1", snapshot) == "Stomping Ground (p5:1)"

    def test_pool_colors_unresolved(self) -> None:
        snapshot = {"players": [{"battlefield": []}]}
        assert _resolve_mana_plan("RED,BLUE", snapshot) == "RED, BLUE"

    def test_no_snapshot(self) -> None:
        assert _resolve_mana_plan("p1,p5:1", None) == "p1, p5:1"

    def test_mixed_ids_and_colors(self) -> None:
        snapshot = {
            "players": [
                {"battlefield": [{"name": "Mountain", "id": "p1"}]},
            ]
        }
        assert _resolve_mana_plan("p1,RED", snapshot) == "Mountain (p1), RED"


class TestChosenBlockManaPlan:
    def test_shows_mana_plan(self) -> None:
        decision = {
            "chosen": 0,
            "chosenArgs": {"choice": "p3", "mana_plan": "p1,p5"},
            "choices": [{"name": "Lightning Bolt", "id": "p3"}],
            "player": "Alice",
            "subsequentActions": [],
        }
        snapshot = {
            "players": [
                {"battlefield": [{"name": "Mountain", "id": "p1"}, {"name": "Forest", "id": "p5"}]},
            ]
        }
        block = _render_chosen_block(decision, snapshot)
        assert "Chosen: Lightning Bolt" in block
        assert "Mana plan: Mountain (p1), Forest (p5)" in block

    def test_shows_auto_tap_false(self) -> None:
        decision = {
            "chosen": 0,
            "chosenArgs": {"choice": "p3", "mana_plan": "p1", "auto_tap": False},
            "choices": [{"name": "Lightning Bolt", "id": "p3"}],
            "player": "Alice",
            "subsequentActions": [],
        }
        snapshot = {
            "players": [
                {"battlefield": [{"name": "Mountain", "id": "p1"}]},
            ]
        }
        block = _render_chosen_block(decision, snapshot)
        assert "auto_tap=false" in block

    def test_no_mana_plan_no_extra_line(self) -> None:
        decision = {
            "chosen": 0,
            "chosenArgs": {"choice": "p3"},
            "choices": [{"name": "Lightning Bolt", "id": "p3"}],
            "player": "Alice",
            "subsequentActions": [],
        }
        block = _render_chosen_block(decision)
        assert "Mana plan" not in block

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
