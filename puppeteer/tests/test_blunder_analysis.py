"""Tests for the blunder analysis script."""

import gzip
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from blunder_analysis import (
    BLUNDER_SCRIPT_VERSION,
    OPUS_MODEL,
    _card_names_in_decision,
    _card_reference_for_decision,
    _chosen_display,
    _collect_card_names,
    _compute_cost,
    _extract_oracle_fields,
    _format_card_ref,
    _format_decisions,
    _parse_json_array,
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
        "id": "game_test_001",
        "gameType": "Two Player Duel",
        "deckType": "Constructed - Standard",
        "totalTurns": 5,
        "winner": "Alice",
        "players": [
            {"name": "Alice", "type": "pilot", "model": "test-model"},
            {"name": "Bob", "type": "pilot", "model": "test-model"},
        ],
        "snapshots": [
            {
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "ts": "2026-01-01T00:00:01.000-08:00",
                "players": [
                    {
                        "name": "Alice",
                        "life": 20,
                        "hand": [{"name": "Mountain"}],
                        "battlefield": [],
                        "graveyard": [],
                        "commanders": [],
                    },
                    {
                        "name": "Bob",
                        "life": 20,
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
    }


def _mock_response(content: str, prompt_tokens: int = 2000, completion_tokens: int = 200) -> MagicMock:
    """Create a mock API response."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.usage = MagicMock(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return response


# --- _parse_json_array ---


class TestParseJsonArray:
    def test_plain_json(self) -> None:
        assert _parse_json_array('[{"a": 1}]') == [{"a": 1}]

    def test_empty_array(self) -> None:
        assert _parse_json_array("[]") == []

    def test_markdown_json_fence(self) -> None:
        text = '```json\n[{"a": 1}]\n```'
        assert _parse_json_array(text) == [{"a": 1}]

    def test_markdown_plain_fence(self) -> None:
        text = "```\n[1, 2, 3]\n```"
        assert _parse_json_array(text) == [1, 2, 3]

    def test_surrounding_text(self) -> None:
        text = 'Here are the results:\n[{"a": 1}]\nDone.'
        assert _parse_json_array(text) == [{"a": 1}]

    def test_rejects_non_array(self) -> None:
        with pytest.raises(AssertionError, match="Expected JSON array"):
            _parse_json_array('{"a": 1}')

    def test_rejects_garbage(self) -> None:
        with pytest.raises(AssertionError, match="No JSON array"):
            _parse_json_array("no json here at all")


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

    def test_none_choice(self) -> None:
        d = _make_decision(chosen=None)
        assert _chosen_display(d) == "?"

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
        # Bob (opponent) should only show hand count
        assert "Bob: 20hp hand=7" in result

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

    def test_extracts_from_choices(self) -> None:
        d = _make_decision()
        names = _card_names_in_decision(d)
        # Choices include Mountain and Lightning Bolt
        assert "Mountain" in names
        assert "Lightning Bolt" in names


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
        return game

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("blunder_analysis._get_oracle_texts", return_value={})
    @patch("blunder_analysis.fetch_openrouter_prices", return_value=_TEST_PRICES)
    @patch("blunder_analysis.OpenAI")
    def test_full_flow_with_blunders(
        self,
        mock_openai_cls: MagicMock,
        _mock_prices: MagicMock,
        _mock_oracle: MagicMock,
        tmp_path: Path,
    ) -> None:
        game = self._make_game_with_decisions()
        gz_path = tmp_path / "game.json.gz"
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
                        "category": "unused_mana",
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
        assert result["annotations"][0]["category"] == "unused_mana"
        assert result["blunderScriptVersion"] == BLUNDER_SCRIPT_VERSION

        # One API call per non-forced decision (this game has 1)
        assert mock_client.chat.completions.create.call_count == 1

        # Verify the call used Opus
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == OPUS_MODEL
        assert "extra_body" not in call_kwargs.kwargs

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("blunder_analysis._get_oracle_texts", return_value={})
    @patch("blunder_analysis.fetch_openrouter_prices", return_value=_TEST_PRICES)
    @patch("blunder_analysis.OpenAI")
    def test_no_blunders_found(
        self,
        mock_openai_cls: MagicMock,
        _mock_prices: MagicMock,
        _mock_oracle: MagicMock,
        tmp_path: Path,
    ) -> None:
        game = self._make_game_with_decisions()
        gz_path = tmp_path / "game.json.gz"
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
    @patch("blunder_analysis.OpenAI")
    def test_skips_current_version(self, mock_openai_cls: MagicMock, tmp_path: Path) -> None:
        game = self._make_game_with_decisions()
        game["annotations"] = [{"existing": True}]
        game["blunderScriptVersion"] = BLUNDER_SCRIPT_VERSION
        gz_path = tmp_path / "game.json.gz"
        self._write_gz(gz_path, game)

        main(str(gz_path))

        # No API calls made at all
        mock_openai_cls.assert_not_called()

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("blunder_analysis._get_oracle_texts", return_value={})
    @patch("blunder_analysis.fetch_openrouter_prices", return_value=_TEST_PRICES)
    @patch("blunder_analysis.OpenAI")
    def test_filters_invalid_snapshot_index(
        self,
        mock_openai_cls: MagicMock,
        _mock_prices: MagicMock,
        _mock_oracle: MagicMock,
        tmp_path: Path,
    ) -> None:
        game = self._make_game_with_decisions()
        gz_path = tmp_path / "game.json.gz"
        self._write_gz(gz_path, game)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        # Returns 2 annotations: one valid (index 0), one out of range (index 999)
        valid_ann = {
            "snapshotIndex": 0,
            "player": "Alice",
            "type": "blunder",
            "severity": "minor",
            "category": "unused_mana",
            "description": "test",
            "actionTaken": "test",
            "betterLine": "test",
        }
        invalid_ann = {**valid_ann, "snapshotIndex": 999}
        mock_client.chat.completions.create.return_value = _mock_response(json.dumps([valid_ann, invalid_ann]))

        main(str(gz_path))

        result = self._read_gz(gz_path)
        assert len(result["annotations"]) == 1
        assert result["annotations"][0]["snapshotIndex"] == 0

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("blunder_analysis._get_oracle_texts", return_value={})
    @patch("blunder_analysis.fetch_openrouter_prices", return_value=_TEST_PRICES)
    @patch("blunder_analysis.OpenAI")
    def test_majority_parse_failure_raises(
        self,
        mock_openai_cls: MagicMock,
        _mock_prices: MagicMock,
        _mock_oracle: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When >50% of decisions fail to parse, raises RuntimeError."""
        game = self._make_game_with_decisions()
        gz_path = tmp_path / "game.json.gz"
        self._write_gz(gz_path, game)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        # Return unparseable garbage for the single decision (1/1 = 100% failure)
        mock_client.chat.completions.create.return_value = _mock_response("not json at all {")

        with pytest.raises(RuntimeError, match="Too many parse failures"):
            main(str(gz_path))

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("blunder_analysis._get_oracle_texts", return_value={})
    @patch("blunder_analysis.fetch_openrouter_prices", return_value=_TEST_PRICES)
    @patch("blunder_analysis.OpenAI")
    def test_reanalyzes_old_version(
        self,
        mock_openai_cls: MagicMock,
        _mock_prices: MagicMock,
        _mock_oracle: MagicMock,
        tmp_path: Path,
    ) -> None:
        game = self._make_game_with_decisions()
        game["annotations"] = []
        # Missing blunderScriptVersion → treated as v1, which is < current
        gz_path = tmp_path / "game.json.gz"
        self._write_gz(gz_path, game)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_response("[]", completion_tokens=10)

        main(str(gz_path))

        # API was called despite existing annotations (old version)
        assert mock_client.chat.completions.create.call_count == 1
