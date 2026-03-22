"""Tests for extract_decisions."""

import json

from magebench.analysis.blunder.extract_decisions import (
    _extract_decisions_v1,
    _extract_decisions_v2,
    _find_spell_cancelled_events,
    _mark_rolled_back_casts,
    _resolve_chosen_index,
    _summarize_snapshot,
    _summarize_stack_item,
)
from magebench.game.game_export_types import (
    BuiltGameExport,
    Snapshot,
    _llm_event_from_dict,
)


def _convert_events(events: list[dict]) -> list:
    """Convert raw event dicts to dataclass instances for testing."""
    return [_llm_event_from_dict(e) for e in events]


class TestSummarizeStackItem:
    def test_string_item(self) -> None:
        assert _summarize_stack_item("Lightning Bolt") == "Lightning Bolt"

    def test_dict_without_targets(self) -> None:
        assert _summarize_stack_item({"name": "Counterspell"}) == "Counterspell"

    def test_dict_with_targets(self) -> None:
        item = {"name": "Lightning Bolt", "targets": ["Goblin Guide"]}
        result = _summarize_stack_item(item)
        assert result == {"name": "Lightning Bolt", "targets": ["Goblin Guide"]}

    def test_dict_with_empty_targets(self) -> None:
        """Empty targets list should return just the name."""
        assert _summarize_stack_item({"name": "Opt", "targets": []}) == "Opt"

    def test_dict_with_empty_name_is_allowed(self) -> None:
        assert _summarize_stack_item({"name": ""}) == ""


def _test_snapshot(**kwargs: object) -> Snapshot:
    """Build a minimal Snapshot for tests, filling in required fields."""
    defaults: dict = {
        "seq": 0,
        "turn": 1,
        "phase": None,
        "step": None,
        "active_player": None,
        "priority_player": None,
        "players": [],
        "stack": [],
    }
    defaults.update(kwargs)
    return Snapshot(**defaults)  # type: ignore[arg-type]


class TestSummarizeSnapshotStack:
    def test_stack_preserves_targets(self) -> None:
        snap = _test_snapshot(
            turn=3,
            phase="PRECOMBAT_MAIN",
            stack=[
                {"name": "Lightning Bolt", "targets": ["Goblin Guide"]},
                {"name": "Counterspell", "targets": ["Lightning Bolt"]},
            ],
        )
        summary = _summarize_snapshot(snap)
        assert summary["stack"] == [
            {"name": "Lightning Bolt", "targets": ["Goblin Guide"]},
            {"name": "Counterspell", "targets": ["Lightning Bolt"]},
        ]

    def test_stack_without_targets(self) -> None:
        snap = _test_snapshot(
            phase="PRECOMBAT_MAIN",
            stack=[{"name": "Opt"}],
        )
        summary = _summarize_snapshot(snap)
        assert summary["stack"] == ["Opt"]

    def test_stack_mixed_items(self) -> None:
        snap = _test_snapshot(
            turn=2,
            phase="PRECOMBAT_MAIN",
            stack=[
                {"name": "Swords to Plowshares", "targets": ["Tarmogoyf"]},
                {"name": "Brainstorm"},
                "Legacy string item",
            ],
        )
        summary = _summarize_snapshot(snap)
        assert summary["stack"] == [
            {"name": "Swords to Plowshares", "targets": ["Tarmogoyf"]},
            "Brainstorm",
            "Legacy string item",
        ]


def _make_decision(
    index: int,
    player: str = "Alice",
    message: str = "Play spells and abilities",
    action_ts: str = "",
    **kwargs: object,
) -> dict:
    d: dict = {
        "decision_index": index,
        "snapshot_index": index,
        "player": player,
        "message": message,
        "action_ts": action_ts,
    }
    d.update(kwargs)
    return d


def _cancel(player: str, ts: str) -> tuple[str, str]:
    return (player, ts)


class TestMarkRolledBackCasts:
    def test_basic_rolled_back_cast(self) -> None:
        """Cast spell -> cost choice -> mana tap -> cancel event."""
        decisions = [
            _make_decision(0, message="Play spells and abilities", action_ts="T01"),
            _make_decision(1, message="You may choose an alternative cost", action_ts="T02"),
            _make_decision(2, message="Choose which mana to produce from Gloomlake Verge", action_ts="T03"),
            _make_decision(3, message="Play spells and abilities", action_ts="T05"),
        ]
        _mark_rolled_back_casts(decisions, [_cancel("Alice", "T04")])

        assert decisions[0].get("cast_rolled_back") is True
        assert decisions[1].get("rolled_back") is True
        assert decisions[2].get("rolled_back") is True
        assert "cast_rolled_back" not in decisions[3]
        assert "rolled_back" not in decisions[3]

    def test_no_cancel_no_marking(self) -> None:
        """Normal decisions without spell cancelled events are untouched."""
        decisions = [
            _make_decision(0, message="Play spells and abilities", action_ts="T01"),
            _make_decision(1, message="You may choose an alternative cost", action_ts="T02"),
            _make_decision(2, message="Play spells and abilities", action_ts="T03"),
        ]
        _mark_rolled_back_casts(decisions, [])

        assert "cast_rolled_back" not in decisions[0]
        assert "rolled_back" not in decisions[1]
        assert "cast_rolled_back" not in decisions[2]

    def test_interleaved_players(self) -> None:
        """Rolled-back cast with opponent decisions interleaved."""
        decisions = [
            _make_decision(0, player="Alice", message="Play spells and abilities", action_ts="T01"),
            _make_decision(1, player="Alice", message="Choose which mana to produce", action_ts="T02"),
            _make_decision(2, player="Bob", message="Play instants and activated abilities", action_ts="T03"),
            _make_decision(3, player="Alice", message="Play spells and abilities", action_ts="T05"),
        ]
        _mark_rolled_back_casts(decisions, [_cancel("Alice", "T04")])

        assert decisions[0].get("cast_rolled_back") is True
        assert decisions[1].get("rolled_back") is True
        # Bob's decision is unaffected
        assert "rolled_back" not in decisions[2]
        assert "cast_rolled_back" not in decisions[2]

    def test_instants_prompt(self) -> None:
        """Cast rolled back from 'Play instants and activated abilities' prompt."""
        decisions = [
            _make_decision(0, message="Play instants and activated abilities", action_ts="T01"),
            _make_decision(1, message="Choose which mana to produce", action_ts="T02"),
            _make_decision(2, message="Play instants and activated abilities", action_ts="T04"),
        ]
        _mark_rolled_back_casts(decisions, [_cancel("Alice", "T03")])

        assert decisions[0].get("cast_rolled_back") is True
        assert decisions[1].get("rolled_back") is True

    def test_multiple_rollbacks_same_game(self) -> None:
        """Two separate rolled-back casts for the same player."""
        decisions = [
            _make_decision(0, message="Play spells and abilities", action_ts="T01"),
            _make_decision(1, message="Choose which mana to produce", action_ts="T02"),
            _make_decision(2, message="Play spells and abilities", action_ts="T04"),
            _make_decision(3, message="You may choose an alternative cost", action_ts="T05"),
            _make_decision(4, message="Play spells and abilities", action_ts="T07"),
        ]
        _mark_rolled_back_casts(
            decisions,
            [_cancel("Alice", "T03"), _cancel("Alice", "T06")],
        )

        assert decisions[0].get("cast_rolled_back") is True
        assert decisions[1].get("rolled_back") is True
        assert decisions[2].get("cast_rolled_back") is True
        assert decisions[3].get("rolled_back") is True
        assert "rolled_back" not in decisions[4]

    def test_only_cast_decision_no_intermediates(self) -> None:
        """Cancel right after the cast decision with no intermediate mana choices."""
        decisions = [
            _make_decision(0, message="Play spells and abilities", action_ts="T01"),
            _make_decision(1, message="Play spells and abilities", action_ts="T03"),
        ]
        _mark_rolled_back_casts(decisions, [_cancel("Alice", "T02")])

        assert decisions[0].get("cast_rolled_back") is True
        assert "rolled_back" not in decisions[1]

    def test_different_players_independent(self) -> None:
        """Cancel events for different players don't interfere."""
        decisions = [
            _make_decision(0, player="Alice", message="Play spells and abilities", action_ts="T01"),
            _make_decision(1, player="Alice", message="Choose mana", action_ts="T02"),
            _make_decision(2, player="Bob", message="Play spells and abilities", action_ts="T03"),
            _make_decision(3, player="Bob", message="Choose mana", action_ts="T04"),
            _make_decision(4, player="Alice", message="Play spells and abilities", action_ts="T06"),
            _make_decision(5, player="Bob", message="Play spells and abilities", action_ts="T07"),
        ]
        _mark_rolled_back_casts(
            decisions,
            [_cancel("Alice", "T05"), _cancel("Bob", "T05")],
        )

        assert decisions[0].get("cast_rolled_back") is True
        assert decisions[1].get("rolled_back") is True
        assert decisions[2].get("cast_rolled_back") is True
        assert decisions[3].get("rolled_back") is True


class TestFindSpellCancelledEvents:
    def test_finds_cancel_in_get_action_choices(self) -> None:
        events = [
            {
                "type": "tool_call",
                "tool": "get_action_choices",
                "player": "Alice",
                "ts": "T01",
                "args": {},
                "result": '{"recent_chat": ["[System] Spell cancelled — mana plan was incorrect."]}',
            },
        ]
        assert _find_spell_cancelled_events(_convert_events(events)) == [("Alice", "T01")]

    def test_finds_cancel_in_choose_action(self) -> None:
        result = '{"action_taken": "selected_0", "recent_chat": ["[System] Spell cancelled — not enough mana."]}'
        events = [
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Bob",
                "ts": "T02",
                "args": {},
                "result": result,
            },
        ]
        assert _find_spell_cancelled_events(_convert_events(events)) == [("Bob", "T02")]

    def test_finds_cancel_in_pass_priority(self) -> None:
        events = [
            {
                "type": "tool_call",
                "tool": "pass_priority",
                "player": "Alice",
                "ts": "T03",
                "args": {},
                "result": '{"recent_chat": ["[System] Spell cancelled — mana plan was incorrect or incomplete."]}',
            },
        ]
        assert _find_spell_cancelled_events(_convert_events(events)) == [("Alice", "T03")]

    def test_ignores_non_system_chat(self) -> None:
        events = [
            {
                "type": "tool_call",
                "tool": "get_action_choices",
                "player": "Alice",
                "ts": "T01",
                "args": {},
                "result": '{"recent_chat": ["Alice: I will cast a spell!"]}',
            },
        ]
        assert _find_spell_cancelled_events(_convert_events(events)) == []

    def test_ignores_non_tool_call(self) -> None:
        events = [
            {
                "type": "llm_response",
                "player": "Alice",
                "ts": "T01",
            },
        ]
        assert _find_spell_cancelled_events(_convert_events(events)) == []

    def test_multiple_cancels(self) -> None:
        events = [
            {
                "type": "tool_call",
                "tool": "get_action_choices",
                "player": "Alice",
                "ts": "T01",
                "args": {},
                "result": '{"recent_chat": ["[System] Spell cancelled — mana plan was incorrect."]}',
            },
            {
                "type": "tool_call",
                "tool": "pass_priority",
                "player": "Bob",
                "ts": "T02",
                "args": {},
                "result": '{"recent_chat": ["[System] Spell cancelled — not enough mana."]}',
            },
        ]
        result = _find_spell_cancelled_events(_convert_events(events))
        assert result == [("Alice", "T01"), ("Bob", "T02")]

    def test_backdates_to_previous_event(self) -> None:
        """Cancel in pass_priority is backdated to the previous tool_call for that player."""
        events = [
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "ts": "T01",
                "args": {},
                "result": '{"action_taken": "selected_0"}',
            },
            {
                "type": "tool_call",
                "tool": "get_action_choices",
                "player": "Bob",
                "ts": "T02",
                "args": {},
                "result": '{"choices": []}',
            },
            {
                "type": "tool_call",
                "tool": "pass_priority",
                "player": "Alice",
                "ts": "T10",
                "args": {},
                "result": '{"recent_chat": ["[System] Spell cancelled — not enough mana."]}',
            },
        ]
        result = _find_spell_cancelled_events(_convert_events(events))
        # Should be backdated to T01 (Alice's previous tool_call), not T10
        assert result == [("Alice", "T01")]


def _v2_pass_priority(
    player: str,
    ts: str,
    choices: list,
    message: str = "Play spells and abilities",
    action_type: str = "GAME_SELECT",
    response_type: str = "select",
    **extra: object,
) -> dict:
    """Build a v2 pass_priority tool_call event with action_pending=true."""
    result = {
        "game_seq": 1,
        "action_type": action_type,
        "response_type": response_type,
        "message": message,
        "action_pending": True,
        "choices": choices,
        **extra,
    }
    return {
        "type": "tool_call",
        "tool": "pass_priority",
        "player": player,
        "ts": ts,
        "args": {},
        "result": json.dumps(result),
    }


def _v2_pass_priority_no_action(player: str, ts: str) -> dict:
    """Build a v2 pass_priority event with action_pending=false."""
    result = {"game_seq": 1, "action_pending": False}
    return {
        "type": "tool_call",
        "tool": "pass_priority",
        "player": player,
        "ts": ts,
        "args": {},
        "result": json.dumps(result),
    }


def _v2_choose_action(player: str, ts: str, args: dict, action_taken: str = "selected_0") -> dict:
    """Build a v2 choose_action tool_call event."""
    result = {"success": True, "action_taken": action_taken}
    return {
        "type": "tool_call",
        "tool": "choose_action",
        "player": player,
        "ts": ts,
        "args": args,
        "result": json.dumps(result),
    }


def _v2_choose_action_with_result(player: str, ts: str, args: dict, result: dict) -> dict:
    """Build a v2 choose_action tool_call event with an explicit result."""
    return {
        "type": "tool_call",
        "tool": "choose_action",
        "player": player,
        "ts": ts,
        "args": args,
        "result": json.dumps(result),
    }


def _v2_llm_response(player: str, ts: str, reasoning: str = "thinking") -> dict:
    return {"type": "llm_response", "player": player, "ts": ts, "reasoning": reasoning}


def _v2_game_data(llm_events: list[dict]) -> BuiltGameExport:
    """Build minimal v2 game data."""
    return _minimal_built_export(
        harness_epoch=20,
        llm_events=_convert_events(llm_events),
    )


def _minimal_built_export(**overrides: object) -> BuiltGameExport:
    """Build a minimal BuiltGameExport with sensible defaults."""
    defaults: dict[str, object] = {
        "version": 9,
        "id": "test_game",
        "timestamp": "",
        "game_type": "Two Player Duel",
        "deck_type": "Constructed - Standard",
        "total_turns": 0,
        "winner": None,
        "harness_epoch": 0,
        "youtube_url": "",
        "players": [],
        "card_images": {},
        "snapshots": [
            Snapshot(
                seq=0,
                ts="T00",
                turn=1,
                phase="PRECOMBAT_MAIN",
                step=None,
                active_player=None,
                priority_player=None,
                players=[],
                stack=[],
            ),
        ],
        "actions": [],
        "llm_events": [],
        "game_over": None,
        "season": 0,
        "tournament": None,
    }
    defaults.update(overrides)
    return BuiltGameExport(**defaults)


class TestResolveChosenIndex:
    def test_index_arg(self) -> None:
        assert _resolve_chosen_index({"index": 2}, [], {}) == 2

    def test_answer_arg(self) -> None:
        assert _resolve_chosen_index({"answer": False}, [], {}) is False

    def test_amount_arg(self) -> None:
        assert _resolve_chosen_index({"amount": 5}, [], {}) == 5

    def test_id_arg(self) -> None:
        choices = [{"name": "A", "id": "p1"}, {"name": "B", "id": "p2"}]
        assert _resolve_chosen_index({"id": "p2"}, choices, {}) == 1

    def test_id_not_found_falls_back_to_action_taken(self) -> None:
        result = _resolve_chosen_index({"id": "p99"}, [], {"action_taken": "selected_3"})
        assert result == 3

    def test_fallback_to_action_taken(self) -> None:
        result = _resolve_chosen_index({"attackers": ["p1"]}, [], {"action_taken": "selected_0"})
        assert result == 0

    def test_id_overrides_index_when_both_present(self) -> None:
        """When both id and index are provided, id wins (matching bridge behavior)."""
        choices = [
            {"name": "Self", "id": "p2", "is_you": True},
            {"name": "Opponent", "id": "p1"},
        ]
        # Model sends index=0 (default) but id=p1 (actual intent)
        result = _resolve_chosen_index({"index": 0, "id": "p1"}, choices, {"action_taken": "selected_target_1"})
        assert result == 1  # id=p1 is at index 1, not index 0

    def test_id_overrides_index_id_not_found_falls_back(self) -> None:
        """When both id and index are present but id doesn't match, use index."""
        choices = [{"name": "A", "id": "p1"}, {"name": "B", "id": "p2"}]
        result = _resolve_chosen_index({"index": 0, "id": "p99"}, choices, {})
        assert result == 0  # id=p99 not found, fall back to index=0

    def test_id_overrides_index_empty_id_uses_index(self) -> None:
        """When id is empty string, index is used directly."""
        choices = [{"name": "A", "id": "p1"}, {"name": "B", "id": "p2"}]
        result = _resolve_chosen_index({"index": 1, "id": ""}, choices, {})
        assert result == 1  # empty id is falsy, use index

    def test_action_taken_selected_target(self) -> None:
        """Fallback handles selected_target_N format."""
        result = _resolve_chosen_index({"attackers": ["p1"]}, [], {"action_taken": "selected_target_2"})
        assert result == 2

    def test_action_taken_selected_ability(self) -> None:
        """Fallback handles selected_ability_N format."""
        result = _resolve_chosen_index({"attackers": ["p1"]}, [], {"action_taken": "selected_ability_0"})
        assert result == 0

    def test_no_resolution(self) -> None:
        assert _resolve_chosen_index({"attackers": ["p1"]}, [], {}) is None

    def test_choice_yes(self) -> None:
        """New unified choice field: 'yes' resolves to True."""
        assert _resolve_chosen_index({"choice": "yes"}, [], {}) is True

    def test_choice_no(self) -> None:
        """New unified choice field: 'no' resolves to False."""
        assert _resolve_chosen_index({"choice": "no"}, [], {}) is False

    def test_choice_true(self) -> None:
        """New unified choice field: 'true' resolves to True."""
        assert _resolve_chosen_index({"choice": "true"}, [], {}) is True

    def test_choice_false(self) -> None:
        """New unified choice field: 'false' resolves to False."""
        assert _resolve_chosen_index({"choice": "false"}, [], {}) is False

    def test_choice_index(self) -> None:
        """New unified choice field: numeric string resolves to int."""
        assert _resolve_chosen_index({"choice": "2"}, [], {}) == 2

    def test_choice_id(self) -> None:
        """New unified choice field: permanent ID resolves to choice index."""
        choices = [{"name": "A", "id": "p1"}, {"name": "B", "id": "p2"}]
        assert _resolve_chosen_index({"choice": "p2"}, choices, {}) == 1

    def test_choice_id_not_found(self) -> None:
        """New unified choice field: unknown ID returns None."""
        choices = [{"name": "A", "id": "p1"}]
        assert _resolve_chosen_index({"choice": "p99"}, choices, {}) is None

    def test_choice_case_insensitive(self) -> None:
        """New unified choice field: boolean keywords are case-insensitive."""
        assert _resolve_chosen_index({"choice": "YES"}, [], {}) is True
        assert _resolve_chosen_index({"choice": "No"}, [], {}) is False

    def test_choice_takes_precedence_over_old_fields(self) -> None:
        """When choice is present alongside old fields, choice wins."""
        choices = [{"name": "A", "id": "p1"}, {"name": "B", "id": "p2"}]
        result = _resolve_chosen_index({"choice": "p2", "index": 0, "answer": True}, choices, {})
        assert result == 1  # choice=p2 at index 1, not index=0 or answer=True


class TestExtractDecisionsV2:
    def test_basic_pass_priority_decision(self) -> None:
        """pass_priority with action_pending -> llm_response -> choose_action."""
        choices = [{"name": "Lightning Bolt", "id": "p1", "index": 0}]
        events = [
            _v2_pass_priority("Alice", "T01", choices),
            _v2_llm_response("Alice", "T02"),
            _v2_choose_action("Alice", "T03", {"id": "p1"}),
        ]
        decisions = _extract_decisions_v2(_v2_game_data(events))
        assert len(decisions) == 1
        d = decisions[0]
        assert d["player"] == "Alice"
        assert d["message"] == "Play spells and abilities"
        assert d["chosen"] == 0
        assert d["chosen_args"] == {"id": "p1"}
        assert d["choice_count"] == 1
        assert d["is_forced"] is False  # "Play spells" allows passing

    def test_multiple_choices_id_resolution(self) -> None:
        """choose_action with id resolves to correct index."""
        choices = [
            {"name": "Forest", "id": "p5", "index": 0},
            {"name": "Bolt", "id": "p10", "index": 1},
            {"name": "Bear", "id": "p15", "index": 2},
        ]
        events = [
            _v2_pass_priority("Alice", "T01", choices),
            _v2_llm_response("Alice", "T02", reasoning="cast the bear"),
            _v2_choose_action("Alice", "T03", {"id": "p15"}, "selected_2"),
        ]
        decisions = _extract_decisions_v2(_v2_game_data(events))
        assert len(decisions) == 1
        assert decisions[0]["chosen"] == 2
        assert decisions[0]["reasoning"] == "cast the bear"
        assert decisions[0]["is_forced"] is False

    def test_answer_based_decision(self) -> None:
        """choose_action with answer=false (passing priority)."""
        choices = [{"name": "Bolt", "id": "p1", "index": 0}]
        events = [
            _v2_pass_priority("Alice", "T01", choices),
            _v2_llm_response("Alice", "T02"),
            _v2_choose_action("Alice", "T03", {"answer": False}),
        ]
        decisions = _extract_decisions_v2(_v2_game_data(events))
        assert len(decisions) == 1
        assert decisions[0]["chosen"] is False

    def test_skips_pass_priority_without_action_pending(self) -> None:
        """pass_priority events without action_pending=true are not decisions."""
        events = [
            _v2_pass_priority_no_action("Alice", "T01"),
            _v2_pass_priority(
                "Alice",
                "T02",
                [{"name": "Bolt", "id": "p1", "index": 0}],
            ),
            _v2_llm_response("Alice", "T03"),
            _v2_choose_action("Alice", "T04", {"id": "p1"}),
        ]
        decisions = _extract_decisions_v2(_v2_game_data(events))
        assert len(decisions) == 1
        assert decisions[0]["action_ts"] == "T04"

    def test_two_players_interleaved(self) -> None:
        """Decisions from two players don't interfere."""
        events = [
            _v2_pass_priority("Alice", "T01", [{"name": "Bolt", "id": "p1", "index": 0}]),
            _v2_llm_response("Alice", "T02"),
            _v2_pass_priority("Bob", "T03", [{"name": "Counter", "id": "p2", "index": 0}]),
            _v2_choose_action("Alice", "T04", {"id": "p1"}),
            _v2_llm_response("Bob", "T05"),
            _v2_choose_action("Bob", "T06", {"id": "p2"}),
        ]
        decisions = _extract_decisions_v2(_v2_game_data(events))
        assert len(decisions) == 2
        assert decisions[0]["player"] == "Alice"
        assert decisions[0]["chosen"] == 0
        assert decisions[1]["player"] == "Bob"
        assert decisions[1]["chosen"] == 0

    def test_sequential_decisions_same_player(self) -> None:
        """Multiple sequential decisions for the same player."""
        events = [
            _v2_pass_priority("Alice", "T01", [{"name": "Forest", "id": "p1", "index": 0}]),
            _v2_llm_response("Alice", "T02"),
            _v2_choose_action("Alice", "T03", {"id": "p1"}),
            _v2_pass_priority(
                "Alice",
                "T04",
                [
                    {"name": "Bolt", "id": "p2", "index": 0},
                    {"name": "Bear", "id": "p3", "index": 1},
                ],
            ),
            _v2_llm_response("Alice", "T05"),
            _v2_choose_action("Alice", "T06", {"id": "p3"}, "selected_1"),
        ]
        decisions = _extract_decisions_v2(_v2_game_data(events))
        assert len(decisions) == 2
        assert decisions[0]["chosen"] == 0
        assert decisions[1]["chosen"] == 1

    def test_decision_without_choose_action(self) -> None:
        """Decision source followed by another decision source (no choose_action)."""
        events = [
            _v2_pass_priority("Alice", "T01", [{"name": "Bolt", "id": "p1", "index": 0}]),
            _v2_llm_response("Alice", "T02"),
            _v2_pass_priority("Alice", "T03", [{"name": "Bear", "id": "p2", "index": 0}]),
            _v2_llm_response("Alice", "T04"),
            _v2_choose_action("Alice", "T05", {"id": "p2"}),
        ]
        decisions = _extract_decisions_v2(_v2_game_data(events))
        assert len(decisions) == 2
        # First decision has no choose_action
        assert decisions[0]["chosen"] is None
        assert decisions[0]["action_ts"] == ""
        # Second has one
        assert decisions[1]["chosen"] == 0

    def test_fallback_to_action_taken(self) -> None:
        """When args don't resolve index, fall back to action_taken."""
        choices = [
            {"name": "A", "id": "p1", "index": 0},
            {"name": "B", "id": "p2", "index": 1},
        ]
        events = [
            _v2_pass_priority("Alice", "T01", choices),
            _v2_llm_response("Alice", "T02"),
            _v2_choose_action("Alice", "T03", {"attackers": ["p1"]}, "selected_0"),
        ]
        decisions = _extract_decisions_v2(_v2_game_data(events))
        assert len(decisions) == 1
        assert decisions[0]["chosen"] == 0

    def test_id_overrides_index_in_full_pipeline(self) -> None:
        """When model sends both id and index, id wins (matching bridge)."""
        choices = [
            {"name": "Self", "id": "p2", "index": 0, "is_you": True},
            {"name": "Opponent", "id": "p1", "index": 1},
        ]
        events = [
            _v2_pass_priority(
                "Alice",
                "T01",
                choices,
                message="Select a player",
                action_type="GAME_TARGET",
                response_type="index",
            ),
            _v2_llm_response("Alice", "T02"),
            # Model sends index=0 (default) but id=p1 (actual intent)
            _v2_choose_action(
                "Alice",
                "T03",
                {"index": 0, "id": "p1", "answer": False, "amount": 0},
                "selected_target_1",
            ),
        ]
        decisions = _extract_decisions_v2(_v2_game_data(events))
        assert len(decisions) == 1
        # id=p1 is at index 1, not the raw index=0
        assert decisions[0]["chosen"] == 1

    def test_get_action_choices_also_collected(self) -> None:
        """Rare get_action_choices with action_pending=true is also a decision source."""
        choices = [{"name": "Opt", "id": "p1", "index": 0}]
        result = json.dumps(
            {
                "action_pending": True,
                "choices": choices,
                "action_type": "GAME_SELECT",
                "response_type": "select",
                "message": "Choose a card",
            }
        )
        events = [
            {
                "type": "tool_call",
                "tool": "get_action_choices",
                "player": "Alice",
                "ts": "T01",
                "args": {},
                "result": result,
            },
            _v2_llm_response("Alice", "T02"),
            _v2_choose_action("Alice", "T03", {"id": "p1"}),
        ]
        data = _v2_game_data(events)
        decisions = _extract_decisions_v2(data)
        assert len(decisions) == 1
        assert decisions[0]["message"] == "Choose a card"

    def test_keeps_successful_retry_after_choose_action_error(self) -> None:
        """A later successful retry should replace the earlier failed attempt."""
        choices = [
            {"description": "Blue", "index": 0},
            {"description": "Black", "index": 1},
        ]
        events = [
            _v2_pass_priority(
                "Alice",
                "T01",
                choices,
                message="Choose color",
                action_type="GAME_CHOOSE_CHOICE",
                response_type="index",
            ),
            _v2_llm_response("Alice", "T02", reasoning="black"),
            _v2_choose_action_with_result(
                "Alice",
                "T03",
                {"choice": "Black"},
                {"error": "Unknown short ID: Black"},
            ),
            _v2_llm_response("Alice", "T04", reasoning="use text"),
            _v2_choose_action_with_result(
                "Alice",
                "T05",
                {"text": "Black"},
                {"success": True, "action_taken": "selected_choice_text_Black"},
            ),
        ]
        decisions = _extract_decisions_v2(_v2_game_data(events))
        assert len(decisions) == 1
        assert decisions[0]["chosen_args"] == {"text": "Black"}
        assert decisions[0]["action_result"] == {
            "success": True,
            "action_taken": "selected_choice_text_Black",
        }
        assert decisions[0]["action_ts"] == "T05"


class TestExtractDecisionsV1:
    def test_basic_v1_decision(self) -> None:
        """Basic v1 format: get_action_choices -> llm_response -> choose_action."""
        choices = [{"name": "Bolt", "index": 0}]
        gac_result = json.dumps(
            {
                "action_pending": True,
                "choices": choices,
                "action_type": "GAME_SELECT",
                "response_type": "select",
                "message": "Play spells and abilities",
            }
        )
        ca_result = json.dumps({"action_taken": "selected_0"})
        events = [
            {
                "type": "tool_call",
                "tool": "get_action_choices",
                "player": "Alice",
                "ts": "T01",
                "args": {},
                "result": gac_result,
            },
            {"type": "llm_response", "player": "Alice", "ts": "T02", "reasoning": "bolt it"},
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "ts": "T03",
                "args": {"index": 0},
                "result": ca_result,
            },
        ]
        data = _minimal_built_export(llm_events=_convert_events(events))
        decisions = _extract_decisions_v1(data)
        assert len(decisions) == 1
        assert decisions[0]["chosen"] == 0
        assert decisions[0]["reasoning"] == "bolt it"

    def test_keeps_successful_retry_after_choose_action_error(self) -> None:
        """A later successful retry should replace the earlier failed attempt."""
        choices = [{"description": "Blue", "index": 0}, {"description": "Black", "index": 1}]
        gac_result = json.dumps(
            {
                "action_pending": True,
                "choices": choices,
                "action_type": "GAME_CHOOSE_CHOICE",
                "response_type": "index",
                "message": "Choose color",
            }
        )
        events = [
            {
                "type": "tool_call",
                "tool": "get_action_choices",
                "player": "Alice",
                "ts": "T01",
                "args": {},
                "result": gac_result,
            },
            {"type": "llm_response", "player": "Alice", "ts": "T02", "reasoning": "black"},
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "ts": "T03",
                "args": {"choice": "Black"},
                "result": json.dumps({"error": "Unknown short ID: Black"}),
            },
            {"type": "llm_response", "player": "Alice", "ts": "T04", "reasoning": "use text"},
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "ts": "T05",
                "args": {"text": "Black"},
                "result": json.dumps({"success": True, "action_taken": "selected_choice_text_Black"}),
            },
        ]
        data = _minimal_built_export(llm_events=_convert_events(events))
        decisions = _extract_decisions_v1(data)
        assert len(decisions) == 1
        assert decisions[0]["chosen_args"] == {"text": "Black"}
        assert decisions[0]["action_result"] == {
            "success": True,
            "action_taken": "selected_choice_text_Black",
        }
        assert decisions[0]["action_ts"] == "T05"
