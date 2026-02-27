"""Tests for the shared decision renderer."""

from puppeteer.decision_renderer import (
    _format_choice,
    _permanent_display,
    _render_card_reference,
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
        assert "Land drops: 0/1" in text

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
        decision = _make_decision(
            subsequent_actions=["Alice casts Lightning Bolt"],
        )
        llm_events = [
            {},  # padding
            {},
            {},
            {},
            {},
            {},
            {},
            {},
            {},
            {},
            {"type": "tool_call", "tool": "pass_priority", "player": "Alice"},
            {"type": "llm_response", "player": "Alice", "reasoning": "I should bolt their face."},
            {"type": "tool_call", "tool": "choose_action", "player": "Alice"},
        ]
        text = render_decision(
            decision,
            snap,
            include_chosen=True,
            llm_events=llm_events,
        )
        assert "Chosen: Lightning Bolt" in text
        assert "Reasoning: I should bolt their face." in text
        assert "After: Alice casts Lightning Bolt" in text

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
        assert "Card Reference:" in text
        assert "Lightning Bolt {R} -- Instant" in text
        assert "3 damage" in text
        # Mountain is a basic land, should not appear in card reference
        card_ref_section = text.split("Card Reference:")[1].split("\n\n")[0]
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
        decision = _make_decision(
            subsequent_actions=[],
        )
        decision["castRolledBack"] = True
        llm_events: list[dict] = [{} for _ in range(13)]
        text = render_decision(
            decision,
            snap,
            include_chosen=True,
            llm_events=llm_events,
        )
        assert "rolled it back" in text.lower() or "rolled back" in text.lower()


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
        assert _permanent_display({"name": "Island"}) == "Island"

    def test_tapped(self) -> None:
        assert _permanent_display({"name": "Mountain", "tapped": True}) == "Mountain (tapped)"

    def test_counters(self) -> None:
        result = _permanent_display({"name": "Thalia", "counters": [{"name": "+1/+1", "count": 2}]})
        assert "Thalia" in result
        assert "+1/+1=2" in result

    def test_power_toughness(self) -> None:
        assert _permanent_display({"name": "Goblin Guide", "pt": "2/2"}) == "Goblin Guide 2/2"

    def test_string_input(self) -> None:
        assert _permanent_display("Island") == "Island"


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
