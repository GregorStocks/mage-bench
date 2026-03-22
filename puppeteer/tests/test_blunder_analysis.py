"""Tests for the blunder analysis script."""

import dataclasses
import gzip
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from openai import OpenAIError

from magebench.game.game_export_types import (
    Action,
    CombatCreature,
    CombatGroup,
    Decision,
    GameExport,
    Snapshot,
    SnapshotPlayer,
    ToolCallEvent,
    json_default,
)
from scripts.analysis.blunder_analysis import (
    BLUNDER_SCRIPT_VERSION,
    OPUS_MODEL,
    BlunderAnalysisError,
    _format_preceding_action,
    eval_decisions,
    evaluate_one_decision,
    init_api,
    main,
)
from scripts.analysis.blunder_context import (
    collect_card_names,
    format_current_turn_actions,
)
from scripts.analysis.blunder_llm import compute_cost, parse_annotation
from scripts.game_exports import load_raw_game_export

# Fake prices for testing
_TEST_PRICES = {
    OPUS_MODEL: (5.0, 25.0),
}


def _make_decision(**overrides: object) -> Decision:
    json_key_by_field = {
        field.name: field.metadata.get("json_key", field.name) for field in dataclasses.fields(Decision)
    }
    d: dict[str, object] = {
        "index": 0,
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
        "is_forced": False,
        "llm_event_indices": [],
        "subsequent_actions": ["Alice plays Mountain"],
        "action_seq": 1,
    }
    d.update({str(json_key_by_field.get(field_name, field_name)): value for field_name, value in overrides.items()})
    return Decision.from_dict(d)


def _make_game() -> dict:
    return {
        "version": 8,
        "id": "game_test_001",
        "timestamp": "2026-01-01T00:00:00-08:00",
        "game_type": "Two Player Duel",
        "deck_type": "Constructed - Standard",
        "total_turns": 5,
        "winner": "Alice",
        "players": [
            {
                "name": "Alice",
                "type": "pilot",
                "model": "test-model",
                "tool_calls_ok": 0,
                "tool_calls_failed": 0,
                "thinking_time_secs": 0.0,
            },
            {
                "name": "Bob",
                "type": "pilot",
                "model": "test-model",
                "tool_calls_ok": 0,
                "tool_calls_failed": 0,
                "thinking_time_secs": 0.0,
            },
        ],
        "card_images": {},
        "snapshots": [
            Snapshot(
                seq=1,
                turn=1,
                phase="PRECOMBAT_MAIN",
                step="PRECOMBAT_MAIN",
                active_player="Alice",
                priority_player="Alice",
                ts="2026-01-01T00:00:01.000-08:00",
                players=[
                    SnapshotPlayer(
                        name="Alice",
                        life=20,
                        library_size=53,
                        hand=[{"name": "Mountain"}],
                        battlefield=[],
                        graveyard=[],
                        commanders=[],
                    ),
                    SnapshotPlayer(
                        name="Bob",
                        life=20,
                        library_size=53,
                        hand=[],
                        battlefield=[{"name": "Grizzly Bears"}],
                        graveyard=[],
                        commanders=[],
                    ),
                ],
                stack=[],
            ),
            Snapshot(
                seq=2,
                turn=1,
                phase="COMBAT",
                step="DECLARE_ATTACKERS",
                active_player="Alice",
                priority_player="Bob",
                ts="2026-01-01T00:00:05.000-08:00",
                players=[
                    SnapshotPlayer(
                        name="Alice",
                        life=20,
                        library_size=52,
                        hand=[],
                        battlefield=[{"name": "Mountain"}],
                        graveyard=[],
                        commanders=[],
                    ),
                    SnapshotPlayer(
                        name="Bob",
                        life=20,
                        library_size=53,
                        hand=[],
                        battlefield=[{"name": "Grizzly Bears"}],
                        graveyard=[],
                        commanders=[],
                    ),
                ],
                stack=[],
            ),
        ],
        "actions": [],
        "llm_events": [],
        "game_over": None,
        "annotations": [],
        "blunder_script_version": 0,
        "harness_epoch": 46,
        "youtube_url": "",
        "season": 1,
        "tournament": None,
    }


def _make_game_ctx(**overrides: object) -> dict:
    ctx: dict = {
        "overview": "Test overview",
        "oracle_texts": {},
        "snapshots": [],
        "actions_by_turn": {},
        "num_players": 2,
        "all_actions": [],
        "decisions": [],
        "preceding_by_index": {},
    }
    ctx.update(overrides)
    return ctx


def _make_snapshot(
    *,
    seq: int,
    turn: int = 1,
    phase: str = "PRECOMBAT_MAIN",
    ts: str | None = None,
) -> Snapshot:
    return Snapshot(
        seq=seq,
        turn=turn,
        phase=phase,
        step=phase,
        active_player="Alice",
        priority_player="Alice",
        players=[
            SnapshotPlayer(
                name="Alice",
                life=20,
                library_size=53,
                hand=[{"name": "Mountain"}],
                battlefield=[],
                graveyard=[],
                commanders=[],
            ),
            SnapshotPlayer(
                name="Bob",
                life=20,
                library_size=53,
                hand=[],
                battlefield=[],
                graveyard=[],
                commanders=[],
            ),
        ],
        stack=[],
        ts=ts,
    )


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


# --- parse_annotation ---


class TestParseAnnotation:
    def test_plain_object(self) -> None:
        assert parse_annotation('{"a": 1}') == {"a": 1}

    def test_null(self) -> None:
        assert parse_annotation("null") is None

    def test_empty_array_compat(self) -> None:
        assert parse_annotation("[]") is None

    def test_single_element_array_compat(self) -> None:
        assert parse_annotation('[{"a": 1}]') == {"a": 1}

    def test_markdown_json_fence(self) -> None:
        text = '```json\n{"a": 1}\n```'
        assert parse_annotation(text) == {"a": 1}

    def test_markdown_null_fence(self) -> None:
        text = "```json\nnull\n```"
        assert parse_annotation(text) is None

    def test_surrounding_text(self) -> None:
        text = 'Here is the result:\n{"a": 1}\nDone.'
        assert parse_annotation(text) == {"a": 1}

    def test_text_with_null(self) -> None:
        assert parse_annotation("The play was reasonable.\n\nnull") is None

    def test_text_with_reasonable(self) -> None:
        assert parse_annotation("This is a reasonable play.") is None

    def test_unquoted_keys(self) -> None:
        text = '{severity: "minor", description: "d", action_taken: "a", better_line: "b"}'
        result = parse_annotation(text)
        assert result is not None
        assert result["severity"] == "minor"

    def test_rejects_garbage(self) -> None:
        with pytest.raises((json.JSONDecodeError, AssertionError)):
            parse_annotation("no json here at all")


# --- format_current_turn_actions ---


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
        decision = _make_decision(turn=1)
        result = format_current_turn_actions(decision, self._actions(), "2026-01-01T00:00:12.000")
        assert "## This Turn" in result
        assert "Alice plays Mountain" in result
        assert "Alice casts Sol Ring from hand" in result

    def test_filters_noise(self) -> None:
        decision = _make_decision(turn=1)
        result = format_current_turn_actions(decision, self._actions(), "2026-01-01T00:00:12.000")
        # "puts from stack" and "skip attack" are noise
        assert "Sol Ring from stack" not in result
        assert "skip attack" not in result

    def test_respects_cutoff_timestamp(self) -> None:
        decision = _make_decision(turn=1)
        # Cutoff before Sol Ring cast
        result = format_current_turn_actions(decision, self._actions(), "2026-01-01T00:00:02.500")
        assert "Alice plays Mountain" in result
        assert "Sol Ring" not in result

    def test_no_actions_yet(self) -> None:
        decision = _make_decision(turn=1)
        # Cutoff before any non-TURN action
        result = format_current_turn_actions(decision, self._actions(), "2026-01-01T00:00:01.500")
        assert "(no actions yet)" in result

    def test_wrong_turn_excluded(self) -> None:
        decision = _make_decision(turn=2)
        result = format_current_turn_actions(decision, self._actions(), "2026-01-01T00:00:20.000")
        assert "Bob plays Forest" in result
        assert "Alice plays Mountain" not in result

    def test_no_turn_returns_empty(self) -> None:
        decision = _make_decision(turn=0)
        assert format_current_turn_actions(decision, self._actions(), "2026-01-01T00:00:10.000") == ""


# --- compute_cost ---


class TestComputeCost:
    def test_opus_million_tokens(self) -> None:
        cost = compute_cost(_TEST_PRICES, OPUS_MODEL, 1_000_000, 1_000_000)
        assert cost == pytest.approx(30.0)

    def test_zero_tokens(self) -> None:
        assert compute_cost(_TEST_PRICES, OPUS_MODEL, 0, 0) == 0.0

    def test_missing_model_raises(self) -> None:
        with pytest.raises(AssertionError, match="No pricing found"):
            compute_cost({}, "unknown/model", 100, 100)


class TestCollectCardNames:
    def test_collects_from_snapshots(self) -> None:
        game = GameExport.from_dict(_make_game())
        names = collect_card_names(game)
        assert "Mountain" in names
        assert "Grizzly Bears" in names

    def test_filters_tokens(self) -> None:
        game = GameExport.from_dict(_make_game())
        snap = game.snapshots[0]
        new_player = dataclasses.replace(snap.players[0], battlefield=[{"name": "Otter Token"}])
        game.snapshots[0] = dataclasses.replace(snap, players=[new_player, snap.players[1]])
        names = collect_card_names(game)
        assert "Otter Token" not in names

    def test_collects_from_snapshot_combat(self) -> None:
        game = GameExport.from_dict(_make_game())
        snap = game.snapshots[0]
        game.snapshots[0] = dataclasses.replace(
            snap,
            combat=[
                CombatGroup(
                    attackers=[CombatCreature(name="Goblin Guide", power="2", toughness="2")],
                    blockers=[CombatCreature(name="Wall of Omens", power="0", toughness="4")],
                    blocked=True,
                    defending="Bob",
                )
            ],
        )
        names = collect_card_names(game)
        assert "Goblin Guide" in names
        assert "Wall of Omens" in names

    def test_collects_from_llm_event_combat(self) -> None:
        game = GameExport.from_dict(_make_game())
        game.llm_events = [
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
        names = collect_card_names(game)
        assert "Ragavan, Nimble Pilferer" in names
        assert "Tarmogoyf" in names


class TestEvalOneDecision:
    @patch("scripts.analysis.blunder_analysis.call_llm")
    def test_uses_shared_aftermath_index(self, mock_call_llm: MagicMock) -> None:
        mock_call_llm.return_value = (
            json.dumps(
                {
                    "severity": "minor",
                    "description": "test",
                    "action_taken": "test",
                    "better_line": "test",
                }
            ),
            2000,
            200,
            0,
        )

        decision = _make_decision(snapshot_index=0, action_seq=1)
        snapshots = [
            _make_snapshot(seq=1),
            _make_snapshot(seq=1),
            _make_snapshot(seq=1),
            _make_snapshot(seq=2, phase="COMBAT"),
        ]

        annotations, _cost, parsed_ok, _raw = evaluate_one_decision(
            MagicMock(),
            OPUS_MODEL,
            _TEST_PRICES,
            "Test overview",
            decision,
            {},
            snapshots,
            {},
            2,
            [],
        )

        assert parsed_ok is True
        assert len(annotations) == 1
        assert annotations[0].snapshot_index == 3


# --- Integration: main with mocked API ---


class TestMainIntegration:
    def _write_gz(self, path: Path, data: dict) -> None:
        with gzip.open(path, "wt") as f:
            json.dump(data, f, default=json_default)

    def _read_export(self, path: Path) -> dict:
        """Read a game export, checking both .json5.gz and .json5 variants."""
        if path.exists():
            return load_raw_game_export(path)
        alt = path.with_suffix("") if path.suffix == ".gz" else Path(str(path) + ".gz")
        return load_raw_game_export(alt)

    def _make_game_with_decisions(self) -> dict:
        """Game with LLM events that produce extractable decisions."""
        game = _make_game()
        game["llm_events"] = [
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
                "snapshot_index": 0,
                "player": "Alice",
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "action_type": "GAME_SELECT",
                "response_type": "select",
                "message": "Play spells",
                "choices": [
                    {"index": 0, "name": "Mountain"},
                    {"index": 1, "name": "Lightning Bolt"},
                ],
                "choice_count": 2,
                "chosen": 0,
                "chosen_args": {"index": 0},
                "action_result": {"success": True},
                "is_forced": False,
                "llm_event_indices": [0, 1, 2],
                "subsequent_actions": [],
            }
        ]
        return game

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("scripts.analysis.blunder_analysis._append_blunder_stats")
    @patch("scripts.analysis.blunder_analysis._auto_ingest_ground_truth")
    @patch("scripts.analysis.blunder_analysis.get_oracle_texts", return_value={})
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
        gz_path = tmp_path / "game_test.json5.gz"
        self._write_gz(gz_path, game)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        # Per-decision response (v6 schema: no llm_reasoning)
        response = _mock_response(
            json.dumps(
                [
                    {
                        "snapshot_index": 0,
                        "player": "Alice",
                        "type": "blunder",
                        "severity": "moderate",
                        "description": "Passed with Mountain in hand and no land played",
                        "action_taken": "Passed priority",
                        "better_line": "Play Mountain for mana development",
                    }
                ]
            )
        )
        mock_client.chat.completions.create.return_value = response

        main(str(gz_path))

        # Verify annotations and version were written
        result = self._read_export(gz_path)
        assert "annotations" in result
        assert len(result["annotations"]) == 1
        assert result["blunder_script_version"] == BLUNDER_SCRIPT_VERSION

        # One API call per non-forced decision (this game has 1)
        assert mock_client.chat.completions.create.call_count == 1

        # Verify the call used Opus
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == OPUS_MODEL
        assert "extra_body" not in call_kwargs.kwargs

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("scripts.analysis.blunder_analysis._append_blunder_stats")
    @patch("scripts.analysis.blunder_analysis._auto_ingest_ground_truth")
    @patch("scripts.analysis.blunder_analysis.get_oracle_texts", return_value={})
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
        gz_path = tmp_path / "game_test.json5.gz"
        self._write_gz(gz_path, game)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_response("[]", completion_tokens=10)

        main(str(gz_path))

        # One API call per non-forced decision
        assert mock_client.chat.completions.create.call_count == 1

        # Empty annotations written (marks game as analyzed)
        result = self._read_export(gz_path)
        assert result["annotations"] == []

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("scripts.analysis.blunder_analysis.OpenAI")
    def test_skips_current_version(self, mock_openai_cls: MagicMock, tmp_path: Path) -> None:
        game = self._make_game_with_decisions()
        game["annotations"] = [
            {
                "decision_index": 0,
                "snapshot_index": 0,
                "player": "Alice",
                "type": "blunder",
                "severity": "minor",
                "description": "existing",
                "action_taken": "existing",
                "better_line": "existing",
            }
        ]
        game["blunder_script_version"] = BLUNDER_SCRIPT_VERSION
        gz_path = tmp_path / "game_test.json5.gz"
        self._write_gz(gz_path, game)

        main(str(gz_path))

        # No API calls made at all
        mock_openai_cls.assert_not_called()

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("scripts.analysis.blunder_analysis._append_blunder_stats")
    @patch("scripts.analysis.blunder_analysis._auto_ingest_ground_truth")
    @patch("scripts.analysis.blunder_analysis.get_oracle_texts", return_value={})
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
        gz_path = tmp_path / "game_test.json5.gz"
        self._write_gz(gz_path, game)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        # LLM returns only severity/description (no snapshot_index/player/type)
        llm_ann = {
            "severity": "minor",
            "description": "test",
            "action_taken": "test",
            "better_line": "test",
        }
        mock_client.chat.completions.create.return_value = _mock_response(json.dumps(llm_ann))

        main(str(gz_path))

        result = self._read_export(gz_path)
        assert len(result["annotations"]) == 1
        ann = result["annotations"][0]
        # These fields are injected server-side
        assert ann["type"] == "blunder"
        assert ann["player"] == "Alice"
        assert ann["decision_index"] == 0
        assert "snapshot_index" in ann

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("scripts.analysis.blunder_analysis._append_blunder_stats")
    @patch("scripts.analysis.blunder_analysis._auto_ingest_ground_truth")
    @patch("scripts.analysis.blunder_analysis.get_oracle_texts", return_value={})
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
        gz_path = tmp_path / "game_test.json5.gz"
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
    @patch("scripts.analysis.blunder_analysis.get_oracle_texts", return_value={})
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
        # Missing blunder_script_version → treated as v1, which is < current
        gz_path = tmp_path / "game_test.json5.gz"
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
    @patch("scripts.analysis.blunder_analysis.get_oracle_texts", return_value={})
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
        """No-op decisions (chosen=None, empty action_result/chosen_args) are skipped."""
        game = _make_game()
        # Use canonical decisions field (modern export format)
        game["decisions"] = [
            # No-op: pass_priority that was ignored by the game
            {
                "index": 0,
                "snapshot_index": 0,
                "player": "Alice",
                "turn": 1,
                "phase": "COMBAT",
                "step": "DECLARE_BLOCKERS",
                "message": "Select blockers",
                "action_type": "GAME_SELECT",
                "response_type": "select",
                "choices": [{"name": "Bear", "index": 0, "id": "p1"}],
                "choice_count": 1,
                "chosen": None,
                "chosen_args": {},
                "action_result": {},
                "is_forced": False,
                "llm_event_indices": [],
                "subsequent_actions": [],
            },
            # Real decision: actual blocker assignment
            {
                "index": 1,
                "snapshot_index": 0,
                "player": "Alice",
                "turn": 1,
                "phase": "COMBAT",
                "step": "DECLARE_BLOCKERS",
                "message": "Select blockers",
                "action_type": "GAME_SELECT",
                "response_type": "select",
                "choices": [{"name": "Bear", "index": 0, "id": "p1"}],
                "choice_count": 1,
                "chosen": None,
                "chosen_args": {"blockers": "p1:p5"},
                "action_result": {"success": True},
                "is_forced": False,
                "llm_event_indices": [],
                "subsequent_actions": [],
            },
        ]
        gz_path = tmp_path / "game_test.json5.gz"
        self._write_gz(gz_path, game)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_response("[]", completion_tokens=10)

        main(str(gz_path))

        # Only 1 API call — the no-op decision was skipped
        assert mock_client.chat.completions.create.call_count == 1


class TestPrecedingAction:
    def test_format_preceding_action(self) -> None:

        preceding = _make_decision(
            index=75,
            message="Play spells and abilities",
            chosen=0,
            choices=[
                {"index": 0, "name": "Evolving Wilds"},
                {"index": 1, "name": "Forest"},
            ],
        )
        result = _format_preceding_action(preceding)
        assert "## Preceding Action" in result
        assert "[Decision 75] Play spells and abilities" in result
        assert "→ Chose: Evolving Wilds" in result

    def test_format_preceding_action_with_no_chosen(self) -> None:

        preceding = _make_decision(
            index=10,
            message="Play instants",
            chosen=None,
        )
        result = _format_preceding_action(preceding)
        assert "[Decision 10] Play instants" in result
        assert "Chose" not in result

    def test_eval_decisions_passes_preceding(self) -> None:
        """eval_decisions should pass the preceding decision to evaluate_one_decision."""
        d0 = _make_decision(index=0, message="First")
        d1 = _make_decision(index=1, message="Second")
        ctx = _make_game_ctx(
            decisions=[d0, d1],
            preceding_by_index={1: d0},
        )

        with patch("scripts.analysis.blunder_analysis.evaluate_one_decision") as mock_eval:
            mock_eval.return_value = ([], 0.0, True, {})
            eval_decisions([d0, d1], ctx, MagicMock(), _TEST_PRICES)

            # d0 should have no preceding, d1 should have d0
            calls_by_idx = {}
            for call in mock_eval.call_args_list:
                di = call.args[4]  # decision is 5th positional arg
                preceding = call.kwargs.get("preceding_decision")
                idx = di.index
                calls_by_idx[idx] = preceding

            assert calls_by_idx[0] is None
            assert calls_by_idx[1] is d0


class TestOperationalFailures:
    @patch(
        "scripts.analysis.blunder_analysis.evaluate_one_decision",
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
        "scripts.analysis.blunder_analysis.evaluate_one_decision",
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
