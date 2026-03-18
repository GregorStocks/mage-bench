"""Tests for the blunder analysis script."""

import gzip
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from openai import OpenAIError

from schemas.game_export_types import Action, ToolCallEvent
from scripts.analysis.blunder_analysis import (
    BLUNDER_SCRIPT_VERSION,
    OPUS_MODEL,
    BlunderAnalysisError,
    _card_names_in_decision,
    _card_reference_for_decision,
    _chosen_display,
    _collect_card_names,
    _compute_cost,
    _extract_oracle_fields,
    _format_card_ref,
    _format_current_turn_actions,
    _format_decisions,
    _parse_annotation,
    eval_decisions,
    init_api,
    main,
)

# Fake prices for testing
_TEST_PRICES = {
    OPUS_MODEL: (5.0, 25.0),
}


def _make_decision(**overrides: object) -> dict:
    d: dict = {
        "decision_index": 0,
        "snapshot_index": 0,
        "player": "Alice",
        "turn": 1,
        "phase": "PRECOMBAT_MAIN",
        "message": "Play spells",
        "action_type": "GAME_SELECT",
        "response_type": "select",
        "choices": [
            {"index": 0, "name": "Mountain"},
            {"index": 1, "name": "Lightning Bolt"},
        ],
        "choice_count": 2,
        "chosen": 0,
        "chosen_args": {"index": 0},
        "action_result": {"success": True},
        "reasoning": "I should play a land.",
        "is_forced": False,
        "game_state": {
            "turn": 1,
            "phase": "PRECOMBAT_MAIN",
            "players": [
                {
                    "name": "Alice",
                    "life": 20,
                    "hand": ["Mountain", "Lightning Bolt"],
                    "hand_count": 2,
                    "battlefield": [],
                },
                {"name": "Bob", "life": 20, "hand_count": 7, "battlefield": ["Grizzly Bears"]},
            ],
        },
        "subsequent_actions": ["Alice plays Mountain"],
    }
    d.update(overrides)
    return d


def _make_game() -> dict:
    return {
        "version": 8,
        "id": "game_test_001",
        "timestamp": "2026-01-01T00:00:00-08:00",
        "gameType": "Two Player Duel",
        "deckType": "Constructed - Standard",
        "totalTurns": 5,
        "winner": "Alice",
        "players": [
            {
                "name": "Alice",
                "type": "pilot",
                "model": "test-model",
                "toolCallsOk": 0,
                "toolCallsFailed": 0,
                "thinkingTimeSecs": 0.0,
            },
            {
                "name": "Bob",
                "type": "pilot",
                "model": "test-model",
                "toolCallsOk": 0,
                "toolCallsFailed": 0,
                "thinkingTimeSecs": 0.0,
            },
        ],
        "cardImages": {},
        "snapshots": [
            {
                "seq": 1,
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "step": "PRECOMBAT_MAIN",
                "active_player": "Alice",
                "priority_player": "Alice",
                "ts": "2026-01-01T00:00:01.000-08:00",
                "players": [
                    {
                        "name": "Alice",
                        "life": 20,
                        "library_size": 53,
                        "hand": [{"name": "Mountain"}],
                        "battlefield": [],
                        "graveyard": [],
                        "commanders": [],
                    },
                    {
                        "name": "Bob",
                        "life": 20,
                        "library_size": 53,
                        "hand": [],
                        "battlefield": [{"name": "Grizzly Bears"}],
                        "graveyard": [],
                        "commanders": [],
                    },
                ],
                "stack": [],
            },
            {
                "seq": 2,
                "turn": 1,
                "phase": "COMBAT",
                "step": "DECLARE_ATTACKERS",
                "active_player": "Alice",
                "priority_player": "Bob",
                "ts": "2026-01-01T00:00:05.000-08:00",
                "players": [
                    {
                        "name": "Alice",
                        "life": 20,
                        "library_size": 52,
                        "hand": [],
                        "battlefield": [{"name": "Mountain"}],
                        "graveyard": [],
                        "commanders": [],
                    },
                    {
                        "name": "Bob",
                        "life": 20,
                        "library_size": 53,
                        "hand": [],
                        "battlefield": [{"name": "Grizzly Bears"}],
                        "graveyard": [],
                        "commanders": [],
                    },
                ],
                "stack": [],
            },
        ],
        "actions": [],
        "llmEvents": [],
        "gameOver": None,
        "annotations": [],
        "blunderScriptVersion": 0,
        "harnessEpoch": 46,
        "youtubeUrl": "",
        "season": 1,
        "tournament": None,
    }


def _make_game_ctx() -> dict:
    return {
        "overview": "Test overview",
        "oracle_texts": {},
        "snapshots": [],
        "actions_by_turn": {},
        "num_players": 2,
        "all_actions": [],
    }


def _mock_response(content: str, prompt_tokens: int = 2000, completion_tokens: int = 200) -> MagicMock:
    """Create a mock API response."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.usage = MagicMock(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_tokens_details=None,
    )
    return response


# --- _parse_annotation ---


class TestParseAnnotation:
    def test_plain_object(self) -> None:
        assert _parse_annotation('{"a": 1}') == {"a": 1}

    def test_null(self) -> None:
        assert _parse_annotation("null") is None

    def test_empty_array_compat(self) -> None:
        assert _parse_annotation("[]") is None

    def test_single_element_array_compat(self) -> None:
        assert _parse_annotation('[{"a": 1}]') == {"a": 1}

    def test_markdown_json_fence(self) -> None:
        text = '```json\n{"a": 1}\n```'
        assert _parse_annotation(text) == {"a": 1}

    def test_markdown_null_fence(self) -> None:
        text = "```json\nnull\n```"
        assert _parse_annotation(text) is None

    def test_surrounding_text(self) -> None:
        text = 'Here is the result:\n{"a": 1}\nDone.'
        assert _parse_annotation(text) == {"a": 1}

    def test_text_with_null(self) -> None:
        assert _parse_annotation("The play was reasonable.\n\nnull") is None

    def test_text_with_reasonable(self) -> None:
        assert _parse_annotation("This is a reasonable play.") is None

    def test_unquoted_keys(self) -> None:
        text = '{severity: "minor", description: "d", actionTaken: "a", betterLine: "b"}'
        result = _parse_annotation(text)
        assert result is not None
        assert result["severity"] == "minor"

    def test_rejects_garbage(self) -> None:
        with pytest.raises((json.JSONDecodeError, AssertionError)):
            _parse_annotation("no json here at all")


# --- _format_current_turn_actions ---


class TestFormatCurrentTurnActions:
    def _actions(self) -> list[Action]:
        return [
            Action(seq=1, ts="2026-01-01T00:00:01.000", message="TURN 1 for Alice (20 - 20)"),
            Action(seq=2, ts="2026-01-01T00:00:02.000", message="Alice plays Mountain"),
            Action(seq=3, ts="2026-01-01T00:00:03.000", message="Alice casts Sol Ring from hand"),
            Action(seq=4, ts="2026-01-01T00:00:04.000", message="Alice puts Sol Ring from stack onto the Battlefield"),
            Action(seq=5, ts="2026-01-01T00:00:10.000", message="Alice skip attack"),
            Action(seq=6, ts="2026-01-01T00:00:15.000", message="TURN 2 for Bob (20 - 20)"),
            Action(seq=7, ts="2026-01-01T00:00:16.000", message="Bob plays Forest"),
        ]

    def test_shows_current_turn_actions(self) -> None:
        decision = {"turn": 1}
        result = _format_current_turn_actions(decision, self._actions(), "2026-01-01T00:00:12.000")
        assert "## This Turn" in result
        assert "Alice plays Mountain" in result
        assert "Alice casts Sol Ring from hand" in result

    def test_filters_noise(self) -> None:
        decision = {"turn": 1}
        result = _format_current_turn_actions(decision, self._actions(), "2026-01-01T00:00:12.000")
        # "puts from stack" and "skip attack" are noise
        assert "Sol Ring from stack" not in result
        assert "skip attack" not in result

    def test_respects_cutoff_timestamp(self) -> None:
        decision = {"turn": 1}
        # Cutoff before Sol Ring cast
        result = _format_current_turn_actions(decision, self._actions(), "2026-01-01T00:00:02.500")
        assert "Alice plays Mountain" in result
        assert "Sol Ring" not in result

    def test_no_actions_yet(self) -> None:
        decision = {"turn": 1}
        # Cutoff before any non-TURN action
        result = _format_current_turn_actions(decision, self._actions(), "2026-01-01T00:00:01.500")
        assert "(no actions yet)" in result

    def test_wrong_turn_excluded(self) -> None:
        decision = {"turn": 2}
        result = _format_current_turn_actions(decision, self._actions(), "2026-01-01T00:00:20.000")
        assert "Bob plays Forest" in result
        assert "Alice plays Mountain" not in result

    def test_no_turn_returns_empty(self) -> None:
        decision = {"turn": None}
        assert _format_current_turn_actions(decision, self._actions(), "2026-01-01T00:00:10.000") == ""


# --- _compute_cost ---


class TestComputeCost:
    def test_opus_million_tokens(self) -> None:
        cost = _compute_cost(_TEST_PRICES, OPUS_MODEL, 1_000_000, 1_000_000)
        assert cost == pytest.approx(30.0)

    def test_zero_tokens(self) -> None:
        assert _compute_cost(_TEST_PRICES, OPUS_MODEL, 0, 0) == 0.0

    def test_missing_model_raises(self) -> None:
        with pytest.raises(AssertionError, match="No pricing found"):
            _compute_cost({}, "unknown/model", 100, 100)


# --- _chosen_display ---


class TestChosenDisplay:
    def test_index_choice(self) -> None:
        d = _make_decision(chosen=1)
        assert _chosen_display(d) == "Lightning Bolt"

    def test_boolean_choice(self) -> None:
        d = _make_decision(chosen=False)
        assert _chosen_display(d) == "False"

    def test_none_choice_no_args(self) -> None:
        d = _make_decision(chosen=None, chosen_args={})
        assert _chosen_display(d) == "(no response)"

    def test_none_choice_with_attackers(self) -> None:
        d = _make_decision(chosen=None, chosenArgs={"attackers": "p5,p12"})
        assert _chosen_display(d) == "Attack with p5, p12"

    def test_none_choice_with_blockers(self) -> None:
        d = _make_decision(chosen=None, chosenArgs={"blockers": "p3:p64"})
        assert _chosen_display(d) == "p3 blocks p64"

    def test_none_choice_with_text(self) -> None:
        d = _make_decision(chosen=None, chosenArgs={"text": "Green"})
        assert _chosen_display(d) == "Text: Green"

    def test_out_of_range(self) -> None:
        d = _make_decision(chosen=99)
        assert _chosen_display(d) == "99"


# --- _format_decisions ---


class TestFormatDecisions:
    def test_skips_forced(self) -> None:
        decisions = [
            _make_decision(decision_index=0, snapshot_index=0, is_forced=True),
            _make_decision(decision_index=1, snapshot_index=5, is_forced=False),
        ]
        result = _format_decisions(decisions)
        assert "[Decision 0" not in result
        assert "[Decision 1, snapshot=5]" in result

    def test_includes_key_fields(self) -> None:
        result = _format_decisions([_make_decision()])
        assert "Alice" in result
        assert "Mountain" in result
        assert "Lightning Bolt" in result
        assert "I should play a land." in result
        assert "Bob" in result

    def test_shows_hand_for_deciding_player_only(self) -> None:
        result = _format_decisions([_make_decision()])
        # Alice (deciding player) should show full hand
        assert "hand=[Mountain, Lightning Bolt]" in result
        # Bob (opponent) should not show hand count (hidden info)
        assert "Bob: 20hp bf=" in result
        assert "hand=" not in result.split("Bob:")[1].split("\n")[0]

    def test_truncates_reasoning(self) -> None:
        long_reasoning = "x" * 1000
        result = _format_decisions([_make_decision(reasoning=long_reasoning)])
        # Should be truncated to 500 chars
        assert "x" * 500 in result
        assert "x" * 501 not in result


# --- Oracle text helpers ---


_BOLT_SCRYFALL = {
    "name": "Lightning Bolt",
    "mana_cost": "{R}",
    "type_line": "Instant",
    "oracle_text": "Lightning Bolt deals 3 damage to any target.",
}

_AJANI_SCRYFALL = {
    "name": "Ajani, Outland Chaperone",
    "mana_cost": "{1}{W}{W}",
    "type_line": "Legendary Planeswalker \u2014 Ajani",
    "oracle_text": "+1: Create a 1/1 Kithkin.\n\u22122: Deal 4 to tapped creature.",
    "loyalty": "3",
}

_BEARS_SCRYFALL = {
    "name": "Grizzly Bears",
    "mana_cost": "{1}{G}",
    "type_line": "Creature \u2014 Bear",
    "oracle_text": "",
    "power": "2",
    "toughness": "2",
}

_DFC_SCRYFALL = {
    "name": "Delver of Secrets // Insectile Aberration",
    "card_faces": [
        {
            "name": "Delver of Secrets",
            "mana_cost": "{U}",
            "type_line": "Creature \u2014 Human Wizard",
            "oracle_text": "At the beginning of your upkeep, look at the top card.",
            "power": "1",
            "toughness": "1",
        },
        {
            "name": "Insectile Aberration",
            "mana_cost": "",
            "type_line": "Creature \u2014 Human Insect",
            "oracle_text": "Flying",
            "power": "3",
            "toughness": "2",
        },
    ],
}


class TestExtractOracleFields:
    def test_creature(self) -> None:
        fields = _extract_oracle_fields(_BEARS_SCRYFALL)
        assert fields["name"] == "Grizzly Bears"
        assert fields["mana_cost"] == "{1}{G}"
        assert fields["power"] == "2"
        assert "loyalty" not in fields

    def test_planeswalker(self) -> None:
        fields = _extract_oracle_fields(_AJANI_SCRYFALL)
        assert fields["loyalty"] == "3"
        assert "power" not in fields

    def test_dfc(self) -> None:
        fields = _extract_oracle_fields(_DFC_SCRYFALL)
        assert len(fields["card_faces"]) == 2
        assert fields["card_faces"][0]["name"] == "Delver of Secrets"
        assert fields["card_faces"][1]["power"] == "3"


class TestFormatCardRef:
    def test_instant(self) -> None:
        ref = _format_card_ref(_extract_oracle_fields(_BOLT_SCRYFALL))
        assert "Lightning Bolt {R}" in ref
        assert "Instant" in ref
        assert "3 damage" in ref

    def test_creature_pt(self) -> None:
        ref = _format_card_ref(_extract_oracle_fields(_BEARS_SCRYFALL))
        assert "2/2" in ref

    def test_planeswalker_loyalty(self) -> None:
        ref = _format_card_ref(_extract_oracle_fields(_AJANI_SCRYFALL))
        assert "[Loyalty: 3]" in ref

    def test_dfc_both_faces(self) -> None:
        ref = _format_card_ref(_extract_oracle_fields(_DFC_SCRYFALL))
        assert "Delver of Secrets" in ref
        assert "Insectile Aberration" in ref
        assert " // " in ref


class TestCardNamesInDecision:
    def test_extracts_from_all_zones(self) -> None:
        d = _make_decision()
        names = _card_names_in_decision(d)
        assert "Mountain" in names
        assert "Lightning Bolt" in names
        assert "Grizzly Bears" in names

    def test_extracts_from_dict_permanents(self) -> None:
        """Dict-form permanents (tapped, counters, etc.) should be extracted."""
        d = _make_decision()
        d["game_state"]["players"][1]["battlefield"] = [
            {"name": "Llanowar Elves", "tapped": True},
            "Forest",
        ]
        names = _card_names_in_decision(d)
        assert "Llanowar Elves" in names
        assert "Forest" in names

    def test_extracts_from_choices(self) -> None:
        d = _make_decision()
        names = _card_names_in_decision(d)
        # Choices include Mountain and Lightning Bolt
        assert "Mountain" in names
        assert "Lightning Bolt" in names

    def test_extracts_from_combat_fields(self) -> None:
        d = _make_decision(
            combat=[
                {
                    "attackers": [{"name": "Goblin Guide", "power": "2", "toughness": "2"}],
                    "blockers": [{"name": "Wall of Omens", "power": "0", "toughness": "4"}],
                    "blocked": True,
                    "defending": "Bob",
                }
            ],
            already_attacking=[{"name": "Monastery Swiftspear", "power": "1", "toughness": "2"}],
            incoming_attackers=[{"name": "Tarmogoyf", "power": "4", "toughness": "5"}],
            game_state={
                "turn": 1,
                "phase": "COMBAT",
                "players": [
                    {"name": "Alice", "life": 20, "hand": [], "battlefield": []},
                    {"name": "Bob", "life": 18, "battlefield": []},
                ],
                "combat": [
                    {
                        "attackers": [{"name": "Ragavan, Nimble Pilferer"}],
                        "blockers": [],
                        "blocked": False,
                        "defending": "Bob",
                    }
                ],
            },
        )
        names = _card_names_in_decision(d)
        assert "Goblin Guide" in names
        assert "Wall of Omens" in names
        assert "Monastery Swiftspear" in names
        assert "Tarmogoyf" in names
        assert "Ragavan, Nimble Pilferer" in names


class TestCardReferenceForDecision:
    def test_builds_reference(self) -> None:
        d = _make_decision()
        oracle = {
            "Lightning Bolt": _extract_oracle_fields(_BOLT_SCRYFALL),
            "Grizzly Bears": _extract_oracle_fields(_BEARS_SCRYFALL),
        }
        ref = _card_reference_for_decision(d, oracle)
        assert "## Card Reference" in ref
        assert "Lightning Bolt" in ref
        assert "Grizzly Bears" in ref

    def test_empty_when_no_matches(self) -> None:
        d = _make_decision()
        ref = _card_reference_for_decision(d, {})
        assert ref == ""


class TestCollectCardNames:
    def test_collects_from_snapshots(self) -> None:
        game = _make_game()
        names = _collect_card_names(game)
        assert "Mountain" in names
        assert "Grizzly Bears" in names

    def test_filters_tokens(self) -> None:
        game = _make_game()
        game["snapshots"][0]["players"][0]["battlefield"] = [{"name": "Otter Token"}]
        names = _collect_card_names(game)
        assert "Otter Token" not in names

    def test_collects_from_snapshot_combat(self) -> None:
        game = _make_game()
        game["snapshots"][0]["combat"] = [
            {
                "attackers": [{"name": "Goblin Guide", "power": "2", "toughness": "2"}],
                "blockers": [{"name": "Wall of Omens", "power": "0", "toughness": "4"}],
                "blocked": True,
                "defending": "Bob",
            }
        ]
        names = _collect_card_names(game)
        assert "Goblin Guide" in names
        assert "Wall of Omens" in names

    def test_collects_from_llm_event_combat(self) -> None:
        game = _make_game()
        game["llmEvents"] = [
            ToolCallEvent(
                type="tool_call",
                player="Alice",
                tool="get_action_choices",
                args={},
                result=json.dumps(
                    {
                        "action_pending": True,
                        "choices": [],
                        "combat": [
                            {
                                "attackers": [{"name": "Ragavan, Nimble Pilferer"}],
                                "blockers": [],
                            }
                        ],
                        "incoming_attackers": [{"name": "Tarmogoyf"}],
                    }
                ),
            )
        ]
        names = _collect_card_names(game)
        assert "Ragavan, Nimble Pilferer" in names
        assert "Tarmogoyf" in names


class TestFormatDecisionsStack:
    def test_shows_stack_with_targets(self) -> None:
        d = _make_decision(
            game_state={
                "turn": 2,
                "phase": "PRECOMBAT_MAIN",
                "players": [
                    {
                        "name": "Alice",
                        "life": 20,
                        "hand": ["Counterspell"],
                        "hand_count": 1,
                        "battlefield": ["Island", "Island"],
                    },
                    {"name": "Bob", "life": 20, "battlefield": []},
                ],
                "stack": [
                    {"name": "Lightning Bolt", "targets": ["Alice"]},
                ],
            },
        )
        result = _format_decisions([d])
        assert "Lightning Bolt -> Alice" in result

    def test_shows_stack_without_targets(self) -> None:
        d = _make_decision(
            game_state={
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "players": [
                    {"name": "Alice", "life": 20, "hand": [], "battlefield": []},
                    {"name": "Bob", "life": 20, "battlefield": []},
                ],
                "stack": ["Brainstorm"],
            },
        )
        result = _format_decisions([d])
        assert "Stack: [Brainstorm]" in result

    def test_stack_multiple_targets(self) -> None:
        d = _make_decision(
            game_state={
                "turn": 3,
                "phase": "PRECOMBAT_MAIN",
                "players": [
                    {"name": "Alice", "life": 20, "hand": [], "battlefield": []},
                    {"name": "Bob", "life": 20, "battlefield": []},
                ],
                "stack": [
                    {"name": "Decimate", "targets": ["Sol Ring", "Birds of Paradise", "Propaganda", "Forest"]},
                ],
            },
        )
        result = _format_decisions([d])
        assert "Decimate -> Sol Ring, Birds of Paradise, Propaganda, Forest" in result


class TestCardNamesInDecisionStack:
    def test_extracts_from_dict_stack_items(self) -> None:
        d = _make_decision(
            game_state={
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "players": [
                    {"name": "Alice", "life": 20, "hand": [], "battlefield": []},
                    {"name": "Bob", "life": 20, "battlefield": []},
                ],
                "stack": [
                    {"name": "Lightning Bolt", "targets": ["Goblin Guide"]},
                ],
            },
        )
        names = _card_names_in_decision(d)
        assert "Lightning Bolt" in names


class TestFormatDecisionsCombat:
    def test_shows_combat_context(self) -> None:
        d = _make_decision(
            phase="COMBAT",
            game_state={
                "turn": 3,
                "phase": "COMBAT",
                "players": [
                    {
                        "name": "Alice",
                        "life": 20,
                        "hand": [],
                        "battlefield": ["Mountain"],
                    },
                    {"name": "Bob", "life": 18, "battlefield": ["Wall of Omens"]},
                ],
                "combat": [
                    {
                        "attackers": [{"name": "Goblin Guide", "power": "2", "toughness": "2"}],
                        "blockers": [{"name": "Wall of Omens", "power": "0", "toughness": "4"}],
                        "blocked": True,
                        "defending": "Bob",
                    }
                ],
            },
        )
        result = _format_decisions([d])
        assert "Combat:" in result
        assert "Goblin Guide" in result
        assert "blocked by Wall of Omens" in result

    def test_shows_combat_phase(self) -> None:
        d = _make_decision(combat_phase="declare_blockers")
        result = _format_decisions([d])
        assert "Combat Phase: declare_blockers" in result


# --- Integration: main with mocked API ---


class TestMainIntegration:
    def _write_gz(self, path: Path, data: dict) -> None:
        with gzip.open(path, "wt") as f:
            json.dump(data, f)

    def _read_gz(self, path: Path) -> dict:
        with gzip.open(path, "rt") as f:
            return json.load(f)

    def _make_game_with_decisions(self) -> dict:
        """Game with LLM events that produce extractable decisions."""
        game = _make_game()
        game["llmEvents"] = [
            {
                "ts": "2026-01-01T00:00:01.500-08:00",
                "player": "Alice",
                "type": "tool_call",
                "tool": "get_action_choices",
                "args": {},
                "result": json.dumps(
                    {
                        "action_pending": True,
                        "action_type": "GAME_SELECT",
                        "response_type": "select",
                        "message": "Play spells",
                        "choices": [
                            {"index": 0, "name": "Mountain"},
                            {"index": 1, "name": "Lightning Bolt"},
                        ],
                    }
                ),
            },
            {
                "ts": "2026-01-01T00:00:01.700-08:00",
                "player": "Alice",
                "type": "llm_response",
                "reasoning": "I will pass without playing anything.",
            },
            {
                "ts": "2026-01-01T00:00:01.800-08:00",
                "player": "Alice",
                "type": "tool_call",
                "tool": "choose_action",
                "args": {"index": 0},
                "result": json.dumps({"success": True}),
            },
        ]
        game["decisions"] = [
            {
                "index": 0,
                "snapshotIndex": 0,
                "player": "Alice",
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "actionType": "GAME_SELECT",
                "responseType": "select",
                "message": "Play spells",
                "choices": [
                    {"index": 0, "name": "Mountain"},
                    {"index": 1, "name": "Lightning Bolt"},
                ],
                "choiceCount": 2,
                "chosen": 0,
                "chosenArgs": {"index": 0},
                "actionResult": {"success": True},
                "isForced": False,
                "llmEventIndices": [0, 1, 2],
                "subsequentActions": [],
            }
        ]
        return game

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("scripts.analysis.blunder_analysis._append_blunder_stats")
    @patch("scripts.analysis.blunder_analysis._auto_ingest_ground_truth")
    @patch("scripts.analysis.blunder_analysis._get_oracle_texts", return_value={})
    @patch("scripts.analysis.blunder_analysis.fetch_openrouter_prices", return_value=_TEST_PRICES)
    @patch("scripts.analysis.blunder_analysis.OpenAI")
    def test_full_flow_with_blunders(
        self,
        mock_openai_cls: MagicMock,
        _mock_prices: MagicMock,
        _mock_oracle: MagicMock,
        _mock_ingest: MagicMock,
        _mock_stats: MagicMock,
        tmp_path: Path,
    ) -> None:
        game = self._make_game_with_decisions()
        gz_path = tmp_path / "game_test.json.gz"
        self._write_gz(gz_path, game)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        # Per-decision response (v6 schema: no llmReasoning)
        response = _mock_response(
            json.dumps(
                [
                    {
                        "snapshotIndex": 0,
                        "player": "Alice",
                        "type": "blunder",
                        "severity": "moderate",
                        "description": "Passed with Mountain in hand and no land played",
                        "actionTaken": "Passed priority",
                        "betterLine": "Play Mountain for mana development",
                    }
                ]
            )
        )
        mock_client.chat.completions.create.return_value = response

        main(str(gz_path))

        # Verify annotations and version were written
        result = self._read_gz(gz_path)
        assert "annotations" in result
        assert len(result["annotations"]) == 1
        assert result["blunderScriptVersion"] == BLUNDER_SCRIPT_VERSION

        # One API call per non-forced decision (this game has 1)
        assert mock_client.chat.completions.create.call_count == 1

        # Verify the call used Opus
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == OPUS_MODEL
        assert "extra_body" not in call_kwargs.kwargs

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("scripts.analysis.blunder_analysis._append_blunder_stats")
    @patch("scripts.analysis.blunder_analysis._auto_ingest_ground_truth")
    @patch("scripts.analysis.blunder_analysis._get_oracle_texts", return_value={})
    @patch("scripts.analysis.blunder_analysis.fetch_openrouter_prices", return_value=_TEST_PRICES)
    @patch("scripts.analysis.blunder_analysis.OpenAI")
    def test_no_blunders_found(
        self,
        mock_openai_cls: MagicMock,
        _mock_prices: MagicMock,
        _mock_oracle: MagicMock,
        _mock_ingest: MagicMock,
        _mock_stats: MagicMock,
        tmp_path: Path,
    ) -> None:
        game = self._make_game_with_decisions()
        gz_path = tmp_path / "game_test.json.gz"
        self._write_gz(gz_path, game)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_response("[]", completion_tokens=10)

        main(str(gz_path))

        # One API call per non-forced decision
        assert mock_client.chat.completions.create.call_count == 1

        # Empty annotations written (marks game as analyzed)
        result = self._read_gz(gz_path)
        assert result["annotations"] == []

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("scripts.analysis.blunder_analysis.OpenAI")
    def test_skips_current_version(self, mock_openai_cls: MagicMock, tmp_path: Path) -> None:
        game = self._make_game_with_decisions()
        game["annotations"] = [
            {
                "decisionIndex": 0,
                "snapshotIndex": 0,
                "player": "Alice",
                "type": "blunder",
                "severity": "minor",
                "description": "existing",
                "actionTaken": "existing",
                "betterLine": "existing",
            }
        ]
        game["blunderScriptVersion"] = BLUNDER_SCRIPT_VERSION
        gz_path = tmp_path / "game_test.json.gz"
        self._write_gz(gz_path, game)

        main(str(gz_path))

        # No API calls made at all
        mock_openai_cls.assert_not_called()

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("scripts.analysis.blunder_analysis._append_blunder_stats")
    @patch("scripts.analysis.blunder_analysis._auto_ingest_ground_truth")
    @patch("scripts.analysis.blunder_analysis._get_oracle_texts", return_value={})
    @patch("scripts.analysis.blunder_analysis.fetch_openrouter_prices", return_value=_TEST_PRICES)
    @patch("scripts.analysis.blunder_analysis.OpenAI")
    def test_injects_metadata_fields(
        self,
        mock_openai_cls: MagicMock,
        _mock_prices: MagicMock,
        _mock_oracle: MagicMock,
        _mock_ingest: MagicMock,
        _mock_stats: MagicMock,
        tmp_path: Path,
    ) -> None:
        game = self._make_game_with_decisions()
        gz_path = tmp_path / "game_test.json.gz"
        self._write_gz(gz_path, game)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        # LLM returns only severity/description (no snapshotIndex/player/type)
        llm_ann = {
            "severity": "minor",
            "description": "test",
            "actionTaken": "test",
            "betterLine": "test",
        }
        mock_client.chat.completions.create.return_value = _mock_response(json.dumps(llm_ann))

        main(str(gz_path))

        result = self._read_gz(gz_path)
        assert len(result["annotations"]) == 1
        ann = result["annotations"][0]
        # These fields are injected server-side
        assert ann["type"] == "blunder"
        assert ann["player"] == "Alice"
        assert ann["decisionIndex"] == 0
        assert "snapshotIndex" in ann

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("scripts.analysis.blunder_analysis._append_blunder_stats")
    @patch("scripts.analysis.blunder_analysis._auto_ingest_ground_truth")
    @patch("scripts.analysis.blunder_analysis._get_oracle_texts", return_value={})
    @patch("scripts.analysis.blunder_analysis.fetch_openrouter_prices", return_value=_TEST_PRICES)
    @patch("scripts.analysis.blunder_analysis.OpenAI")
    def test_majority_parse_failure_raises(
        self,
        mock_openai_cls: MagicMock,
        _mock_prices: MagicMock,
        _mock_oracle: MagicMock,
        _mock_ingest: MagicMock,
        _mock_stats: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When >50% of decisions fail to parse, raises RuntimeError."""
        game = self._make_game_with_decisions()
        gz_path = tmp_path / "game_test.json.gz"
        self._write_gz(gz_path, game)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        # Return unparseable garbage for the single decision (1/1 = 100% failure)
        mock_client.chat.completions.create.return_value = _mock_response("not json at all {")

        with pytest.raises(BlunderAnalysisError, match="Too many parse failures"):
            main(str(gz_path))

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("scripts.analysis.blunder_analysis._append_blunder_stats")
    @patch("scripts.analysis.blunder_analysis._auto_ingest_ground_truth")
    @patch("scripts.analysis.blunder_analysis._get_oracle_texts", return_value={})
    @patch("scripts.analysis.blunder_analysis.fetch_openrouter_prices", return_value=_TEST_PRICES)
    @patch("scripts.analysis.blunder_analysis.OpenAI")
    def test_reanalyzes_old_version(
        self,
        mock_openai_cls: MagicMock,
        _mock_prices: MagicMock,
        _mock_oracle: MagicMock,
        _mock_ingest: MagicMock,
        _mock_stats: MagicMock,
        tmp_path: Path,
    ) -> None:
        game = self._make_game_with_decisions()
        game["annotations"] = []
        # Missing blunderScriptVersion → treated as v1, which is < current
        gz_path = tmp_path / "game_test.json.gz"
        self._write_gz(gz_path, game)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_response("[]", completion_tokens=10)

        main(str(gz_path))

        # API was called despite existing annotations (old version)
        assert mock_client.chat.completions.create.call_count == 1

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("scripts.analysis.blunder_analysis._append_blunder_stats")
    @patch("scripts.analysis.blunder_analysis._auto_ingest_ground_truth")
    @patch("scripts.analysis.blunder_analysis._get_oracle_texts", return_value={})
    @patch("scripts.analysis.blunder_analysis.fetch_openrouter_prices", return_value=_TEST_PRICES)
    @patch("scripts.analysis.blunder_analysis.OpenAI")
    def test_skips_noop_decisions(
        self,
        mock_openai_cls: MagicMock,
        _mock_prices: MagicMock,
        _mock_oracle: MagicMock,
        _mock_ingest: MagicMock,
        _mock_stats: MagicMock,
        tmp_path: Path,
    ) -> None:
        """No-op decisions (chosen=None, empty actionResult/chosenArgs) are skipped."""
        game = _make_game()
        # Use canonical decisions field (modern export format)
        game["decisions"] = [
            # No-op: pass_priority that was ignored by the game
            {
                "index": 0,
                "snapshotIndex": 0,
                "player": "Alice",
                "turn": 1,
                "phase": "COMBAT",
                "step": "DECLARE_BLOCKERS",
                "message": "Select blockers",
                "actionType": "GAME_SELECT",
                "responseType": "select",
                "choices": [{"name": "Bear", "index": 0, "id": "p1"}],
                "choiceCount": 1,
                "chosen": None,
                "chosenArgs": {},
                "actionResult": {},
                "isForced": False,
                "llmEventIndices": [],
                "subsequentActions": [],
            },
            # Real decision: actual blocker assignment
            {
                "index": 1,
                "snapshotIndex": 0,
                "player": "Alice",
                "turn": 1,
                "phase": "COMBAT",
                "step": "DECLARE_BLOCKERS",
                "message": "Select blockers",
                "actionType": "GAME_SELECT",
                "responseType": "select",
                "choices": [{"name": "Bear", "index": 0, "id": "p1"}],
                "choiceCount": 1,
                "chosen": None,
                "chosenArgs": {"blockers": "p1:p5"},
                "actionResult": {"success": True},
                "isForced": False,
                "llmEventIndices": [],
                "subsequentActions": [],
            },
        ]
        gz_path = tmp_path / "game_test.json.gz"
        self._write_gz(gz_path, game)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_response("[]", completion_tokens=10)

        main(str(gz_path))

        # Only 1 API call — the no-op decision was skipped
        assert mock_client.chat.completions.create.call_count == 1


class TestOperationalFailures:
    @patch(
        "scripts.analysis.blunder_analysis._eval_one_decision",
        side_effect=OpenAIError("temporary upstream failure"),
    )
    def test_eval_decisions_continues_on_openai_error(self, _mock_eval: MagicMock) -> None:
        results = eval_decisions(
            [_make_decision()],
            _make_game_ctx(),
            MagicMock(),
            _TEST_PRICES,
        )

        assert results[0] == ([], 0.0, False, {})

    @patch(
        "scripts.analysis.blunder_analysis._eval_one_decision",
        side_effect=AssertionError("unexpected bug"),
    )
    def test_eval_decisions_propagates_non_openai_error(self, _mock_eval: MagicMock) -> None:
        with pytest.raises(AssertionError, match="unexpected bug"):
            eval_decisions(
                [_make_decision()],
                _make_game_ctx(),
                MagicMock(),
                _TEST_PRICES,
            )

    def test_init_api_requires_api_key(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(
                BlunderAnalysisError,
                match="OPENROUTER_API_KEY environment variable required",
            ),
        ):
            init_api()

    @patch("scripts.analysis.blunder_analysis.fetch_openrouter_prices", return_value={})
    def test_init_api_requires_pricing(self, _mock_prices: MagicMock) -> None:
        with (
            patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-test"}, clear=True),
            pytest.raises(
                BlunderAnalysisError,
                match="Could not fetch pricing",
            ),
        ):
            init_api()
