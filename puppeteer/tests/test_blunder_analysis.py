"""Tests for the blunder analysis script."""

import gzip
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from blunder_analysis import (
    BLUNDER_SCRIPT_VERSION,
    _chosen_display,
    _compute_cost,
    _format_decisions,
    _parse_json_array,
    main,
)


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
        cost = _compute_cost("anthropic/claude-opus-4.6", 1_000_000, 1_000_000)
        assert cost == pytest.approx(30.0)

    def test_zero_tokens(self) -> None:
        assert _compute_cost("anthropic/claude-opus-4.6", 0, 0) == 0.0


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
    @patch("blunder_analysis.OpenAI")
    def test_full_flow_with_blunders(self, mock_openai_cls: MagicMock, tmp_path: Path) -> None:
        game = self._make_game_with_decisions()
        gz_path = tmp_path / "game.json.gz"
        self._write_gz(gz_path, game)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        opus_response = MagicMock()
        opus_response.choices = [MagicMock()]
        opus_response.choices[0].message.content = json.dumps(
            [
                {
                    "snapshotIndex": 0,
                    "player": "Alice",
                    "type": "blunder",
                    "severity": "moderate",
                    "category": "unused_mana",
                    "description": "Passed with Mountain in hand and no land played",
                    "llmReasoning": "Model said 'I will pass' but had castable cards",
                    "actionTaken": "Passed priority",
                    "betterLine": "Play Mountain for mana development",
                }
            ]
        )
        opus_response.usage = MagicMock(prompt_tokens=2000, completion_tokens=200)

        mock_client.chat.completions.create.return_value = opus_response

        main(str(gz_path))

        # Verify annotations and version were written
        result = self._read_gz(gz_path)
        assert "annotations" in result
        assert len(result["annotations"]) == 1
        assert result["annotations"][0]["category"] == "unused_mana"
        assert result["blunderScriptVersion"] == BLUNDER_SCRIPT_VERSION

        # Single API call (Opus only)
        assert mock_client.chat.completions.create.call_count == 1

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("blunder_analysis.OpenAI")
    def test_no_blunders_found(self, mock_openai_cls: MagicMock, tmp_path: Path) -> None:
        game = self._make_game_with_decisions()
        gz_path = tmp_path / "game.json.gz"
        self._write_gz(gz_path, game)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        opus_response = MagicMock()
        opus_response.choices = [MagicMock()]
        opus_response.choices[0].message.content = "[]"
        opus_response.usage = MagicMock(prompt_tokens=2000, completion_tokens=10)

        mock_client.chat.completions.create.return_value = opus_response

        main(str(gz_path))

        # Single API call
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
    @patch("blunder_analysis.OpenAI")
    def test_filters_invalid_snapshot_index(self, mock_openai_cls: MagicMock, tmp_path: Path) -> None:
        game = self._make_game_with_decisions()
        gz_path = tmp_path / "game.json.gz"
        self._write_gz(gz_path, game)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        # Opus returns 2 annotations: one valid (index 0), one out of range (index 999)
        opus_response = MagicMock()
        opus_response.choices = [MagicMock()]
        valid_ann = {
            "snapshotIndex": 0,
            "player": "Alice",
            "type": "blunder",
            "severity": "minor",
            "category": "unused_mana",
            "description": "test",
            "llmReasoning": "test",
            "actionTaken": "test",
            "betterLine": "test",
        }
        invalid_ann = {**valid_ann, "snapshotIndex": 999}
        opus_response.choices[0].message.content = json.dumps([valid_ann, invalid_ann])
        opus_response.usage = MagicMock(prompt_tokens=2000, completion_tokens=200)

        mock_client.chat.completions.create.return_value = opus_response

        main(str(gz_path))

        result = self._read_gz(gz_path)
        assert len(result["annotations"]) == 1
        assert result["annotations"][0]["snapshotIndex"] == 0

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("blunder_analysis.OpenAI")
    def test_retries_on_invalid_json(self, mock_openai_cls: MagicMock, tmp_path: Path) -> None:
        game = self._make_game_with_decisions()
        gz_path = tmp_path / "game.json.gz"
        self._write_gz(gz_path, game)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        bad_response = MagicMock()
        bad_response.choices = [MagicMock()]
        bad_response.choices[0].message.content = '[{"a": 1}]\n[{"b": 2}]'
        bad_response.usage = MagicMock(prompt_tokens=1000, completion_tokens=100)

        good_response = MagicMock()
        good_response.choices = [MagicMock()]
        good_response.choices[0].message.content = "[]"
        good_response.usage = MagicMock(prompt_tokens=1000, completion_tokens=10)

        mock_client.chat.completions.create.side_effect = [bad_response, good_response]

        main(str(gz_path))

        # Two API calls: first failed JSON parse, second succeeded
        assert mock_client.chat.completions.create.call_count == 2
        result = self._read_gz(gz_path)
        assert result["annotations"] == []

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("blunder_analysis.OpenAI")
    def test_retry_exhaustion_raises(self, mock_openai_cls: MagicMock, tmp_path: Path) -> None:
        game = self._make_game_with_decisions()
        gz_path = tmp_path / "game.json.gz"
        self._write_gz(gz_path, game)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        bad_response = MagicMock()
        bad_response.choices = [MagicMock()]
        bad_response.choices[0].message.content = "not json at all {"
        bad_response.usage = MagicMock(prompt_tokens=1000, completion_tokens=100)

        mock_client.chat.completions.create.return_value = bad_response

        with pytest.raises(RuntimeError, match="invalid JSON on all 3 attempts"):
            main(str(gz_path))

        assert mock_client.chat.completions.create.call_count == 3

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("blunder_analysis.OpenAI")
    def test_reanalyzes_old_version(self, mock_openai_cls: MagicMock, tmp_path: Path) -> None:
        game = self._make_game_with_decisions()
        game["annotations"] = []
        # Missing blunderScriptVersion → treated as v1, which is < current
        gz_path = tmp_path / "game.json.gz"
        self._write_gz(gz_path, game)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        opus_response = MagicMock()
        opus_response.choices = [MagicMock()]
        opus_response.choices[0].message.content = "[]"
        opus_response.usage = MagicMock(prompt_tokens=2000, completion_tokens=10)

        mock_client.chat.completions.create.return_value = opus_response

        main(str(gz_path))

        # API was called despite existing annotations (old version)
        assert mock_client.chat.completions.create.call_count == 1
