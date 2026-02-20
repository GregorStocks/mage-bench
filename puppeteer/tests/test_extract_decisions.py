"""Tests for extract_decisions."""

from extract_decisions import (
    _find_spell_cancelled_events,
    _mark_rolled_back_casts,
    _summarize_snapshot,
    _summarize_stack_item,
)


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


class TestSummarizeSnapshotStack:
    def test_stack_preserves_targets(self) -> None:
        snap = {
            "turn": 3,
            "phase": "PRECOMBAT_MAIN",
            "players": [],
            "stack": [
                {"name": "Lightning Bolt", "targets": ["Goblin Guide"]},
                {"name": "Counterspell", "targets": ["Lightning Bolt"]},
            ],
        }
        summary = _summarize_snapshot(snap)
        assert summary["stack"] == [
            {"name": "Lightning Bolt", "targets": ["Goblin Guide"]},
            {"name": "Counterspell", "targets": ["Lightning Bolt"]},
        ]

    def test_stack_without_targets(self) -> None:
        snap = {
            "turn": 1,
            "phase": "PRECOMBAT_MAIN",
            "players": [],
            "stack": [{"name": "Opt"}],
        }
        summary = _summarize_snapshot(snap)
        assert summary["stack"] == ["Opt"]

    def test_stack_mixed_items(self) -> None:
        snap = {
            "turn": 2,
            "phase": "PRECOMBAT_MAIN",
            "players": [],
            "stack": [
                {"name": "Swords to Plowshares", "targets": ["Tarmogoyf"]},
                {"name": "Brainstorm"},
                "Legacy string item",
            ],
        }
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
                "result": '{"recent_chat": ["[System] Spell cancelled — mana plan was incorrect."]}',
            },
        ]
        assert _find_spell_cancelled_events(events) == [("Alice", "T01")]

    def test_finds_cancel_in_choose_action(self) -> None:
        result = '{"action_taken": "selected_0", "recent_chat": ["[System] Spell cancelled — not enough mana."]}'
        events = [
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Bob",
                "ts": "T02",
                "result": result,
            },
        ]
        assert _find_spell_cancelled_events(events) == [("Bob", "T02")]

    def test_finds_cancel_in_pass_priority(self) -> None:
        events = [
            {
                "type": "tool_call",
                "tool": "pass_priority",
                "player": "Alice",
                "ts": "T03",
                "result": '{"recent_chat": ["[System] Spell cancelled — mana plan was incorrect or incomplete."]}',
            },
        ]
        assert _find_spell_cancelled_events(events) == [("Alice", "T03")]

    def test_ignores_non_system_chat(self) -> None:
        events = [
            {
                "type": "tool_call",
                "tool": "get_action_choices",
                "player": "Alice",
                "ts": "T01",
                "result": '{"recent_chat": ["Alice: I will cast a spell!"]}',
            },
        ]
        assert _find_spell_cancelled_events(events) == []

    def test_ignores_non_tool_call(self) -> None:
        events = [
            {
                "type": "llm_response",
                "player": "Alice",
                "ts": "T01",
                "result": '{"recent_chat": ["[System] Spell cancelled"]}',
            },
        ]
        assert _find_spell_cancelled_events(events) == []

    def test_multiple_cancels(self) -> None:
        events = [
            {
                "type": "tool_call",
                "tool": "get_action_choices",
                "player": "Alice",
                "ts": "T01",
                "result": '{"recent_chat": ["[System] Spell cancelled — mana plan was incorrect."]}',
            },
            {
                "type": "tool_call",
                "tool": "pass_priority",
                "player": "Bob",
                "ts": "T02",
                "result": '{"recent_chat": ["[System] Spell cancelled — not enough mana."]}',
            },
        ]
        result = _find_spell_cancelled_events(events)
        assert result == [("Alice", "T01"), ("Bob", "T02")]

    def test_backdates_to_previous_event(self) -> None:
        """Cancel in pass_priority is backdated to the previous tool_call for that player."""
        events = [
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "ts": "T01",
                "result": '{"action_taken": "selected_0"}',
            },
            {
                "type": "tool_call",
                "tool": "get_action_choices",
                "player": "Bob",
                "ts": "T02",
                "result": '{"choices": []}',
            },
            {
                "type": "tool_call",
                "tool": "pass_priority",
                "player": "Alice",
                "ts": "T10",
                "result": '{"recent_chat": ["[System] Spell cancelled — not enough mana."]}',
            },
        ]
        result = _find_spell_cancelled_events(events)
        # Should be backdated to T01 (Alice's previous tool_call), not T10
        assert result == [("Alice", "T01")]
