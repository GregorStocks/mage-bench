"""Focused tests for live export helpers and decision backfills."""

import gzip
import json
from pathlib import Path
from unittest.mock import patch

from magebench.common.json5_utils import loads_json5
from puppeteer.harness_epoch import SEASON_1_START_EPOCH
from schemas.game_export_types import Action, Choice, MultiAmountItem, PilotContext
from scripts.backfill_decisions import backfill_game
from scripts.export_card_data import _collect_card_names, _trim_card
from scripts.export_decisions import build_decisions
from scripts.export_game import _compute_season
from scripts.game_exports import (
    glob_game_export_paths,
    load_raw_game_export,
    write_raw_game_export,
)


def _make_stub_export(game_id: str = "game_20260301_120000") -> dict:
    return {"version": 8, "id": game_id}


def test_compute_season_uses_harness_epoch_boundary() -> None:
    assert _compute_season(SEASON_1_START_EPOCH - 1) == 0
    assert _compute_season(SEASON_1_START_EPOCH) == 1


def test_trim_card_extracts_correct_fields() -> None:
    full_card = {
        "name": "Lightning Bolt",
        "mana_cost": "{R}",
        "type_line": "Instant",
        "oracle_text": "Lightning Bolt deals 3 damage to any target.",
        "cmc": 1.0,
        "colors": ["R"],
        "set": "lea",
        "rarity": "common",
        "image_uris": {"small": "https://..."},
        "power": None,
        "toughness": None,
    }

    trimmed = _trim_card(full_card)

    assert set(trimmed.keys()) == {"mana_cost", "type_line", "oracle_text"}
    assert trimmed["mana_cost"] == "{R}"
    assert "power" not in trimmed
    assert "toughness" not in trimmed


def test_trim_card_includes_creature_stats() -> None:
    creature = {
        "name": "Grizzly Bears",
        "mana_cost": "{1}{G}",
        "type_line": "Creature — Bear",
        "oracle_text": "",
        "power": "2",
        "toughness": "2",
    }

    trimmed = _trim_card(creature)

    assert trimmed["power"] == "2"
    assert trimmed["toughness"] == "2"


def test_collect_card_names_separates_tokens_and_cards() -> None:
    snapshots = [
        {
            "seq": 1,
            "turn": 1,
            "phase": "MAIN",
            "step": "MAIN",
            "active_player": "A",
            "priority_player": "A",
            "players": [
                {
                    "name": "A",
                    "life": 20,
                    "library_size": 50,
                    "hand": [{"name": "Lightning Bolt"}],
                    "battlefield": [
                        {"name": "Mountain"},
                        {"name": "Goblin Token"},
                    ],
                    "graveyard": ["Shock"],
                    "exile": [],
                }
            ],
            "stack": [],
        }
    ]

    real_cards, tokens = _collect_card_names(snapshots)

    assert "Lightning Bolt" in real_cards
    assert "Mountain" in real_cards
    assert "Shock" in real_cards
    assert "Goblin Token" in tokens
    assert "Goblin Token" not in real_cards


def test_build_decisions_keeps_successful_retry_and_skips_blank_follow_up() -> None:
    snapshots = [
        {
            "seq": 10,
            "turn": 1,
            "phase": "PRECOMBAT_MAIN",
            "step": "PRECOMBAT_MAIN",
            "players": [],
            "stack": [],
        },
        {
            "seq": 11,
            "turn": 1,
            "phase": "PRECOMBAT_MAIN",
            "step": "PRECOMBAT_MAIN",
            "players": [],
            "stack": [],
        },
        {
            "seq": 12,
            "turn": 1,
            "phase": "PRECOMBAT_MAIN",
            "step": "PRECOMBAT_MAIN",
            "players": [],
            "stack": [],
        },
    ]
    llm_events = [
        {
            "type": "tool_call",
            "tool": "pass_priority",
            "player": "Alice",
            "ts": "T01",
            "gameSeq": 10,
            "args": {},
            "result": json.dumps(
                {
                    "action_pending": True,
                    "action_type": "GAME_SELECT",
                    "response_type": "index",
                    "message": "Choose replacement effect",
                    "choices": [{"index": 0, "description": "Pick effect"}],
                }
            ),
        },
        {
            "type": "llm_response",
            "player": "Alice",
            "ts": "T02",
            "reasoning": "pick the effect",
        },
        {
            "type": "tool_call",
            "tool": "choose_action",
            "player": "Alice",
            "ts": "T03",
            "gameSeq": 11,
            "args": {"choice": "0"},
            "result": json.dumps(
                {
                    "action_pending": True,
                    "action_type": "GAME_CHOOSE_CHOICE",
                    "response_type": "index",
                    "message": "Choose color",
                    "choices": [
                        {"index": 0, "description": "Blue"},
                        {"index": 1, "description": "Black"},
                    ],
                    "success": True,
                    "action_taken": "selected_0",
                }
            ),
        },
        {
            "type": "llm_response",
            "player": "Alice",
            "ts": "T04",
            "reasoning": "black",
        },
        {
            "type": "tool_call",
            "tool": "choose_action",
            "player": "Alice",
            "ts": "T05",
            "gameSeq": 11,
            "args": {"choice": "Black"},
            "result": json.dumps({"error": "Unknown short ID: Black"}),
        },
        {
            "type": "llm_response",
            "player": "Alice",
            "ts": "T06",
            "reasoning": "use text",
        },
        {
            "type": "tool_call",
            "tool": "choose_action",
            "player": "Alice",
            "ts": "T07",
            "gameSeq": 12,
            "args": {"text": "Black"},
            "result": json.dumps(
                {
                    "action_pending": True,
                    "action_type": "GAME_SELECT",
                    "response_type": "boolean",
                    "message": "Play spells and abilities",
                    "choices": [],
                    "success": True,
                    "action_taken": "selected_choice_text_Black",
                }
            ),
        },
    ]

    decisions = build_decisions(snapshots, [], llm_events, harness_epoch=40)

    assert len(decisions) == 2
    assert decisions[1]["message"] == "Choose color"
    assert decisions[1]["chosenArgs"] == {"text": "Black"}
    assert decisions[1]["actionResult"]["action_taken"] == "selected_choice_text_Black"
    assert decisions[1]["actionSeq"] == 12
    assert decisions[1]["llmEventIndices"] == [2, 3, 4, 5, 6]


def test_backfill_game_force_rebuilds_existing_decisions(tmp_path: Path) -> None:
    path = tmp_path / "game_retry.json5"
    payload = {
        "version": 8,
        "id": "game_retry",
        "harnessEpoch": 40,
        "snapshots": [
            {
                "seq": 10,
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "step": "PRECOMBAT_MAIN",
                "active_player": "Alice",
                "priority_player": "Alice",
                "players": [],
                "stack": [],
            },
            {
                "seq": 11,
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "step": "PRECOMBAT_MAIN",
                "active_player": "Alice",
                "priority_player": "Alice",
                "players": [],
                "stack": [],
            },
        ],
        "actions": [],
        "llmEvents": [
            {
                "type": "tool_call",
                "tool": "pass_priority",
                "player": "Alice",
                "ts": "T01",
                "gameSeq": 10,
                "args": {},
                "result": json.dumps(
                    {
                        "action_pending": True,
                        "action_type": "GAME_CHOOSE_CHOICE",
                        "response_type": "index",
                        "message": "Choose color",
                        "choices": [
                            {"index": 0, "description": "Blue"},
                            {"index": 1, "description": "Black"},
                        ],
                    }
                ),
            },
            {
                "type": "llm_response",
                "player": "Alice",
                "ts": "T02",
                "reasoning": "black",
            },
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "ts": "T03",
                "gameSeq": 10,
                "args": {"choice": "Black"},
                "result": json.dumps({"error": "Unknown short ID: Black"}),
            },
            {
                "type": "llm_response",
                "player": "Alice",
                "ts": "T04",
                "reasoning": "use text",
            },
            {
                "type": "tool_call",
                "tool": "choose_action",
                "player": "Alice",
                "ts": "T05",
                "gameSeq": 11,
                "args": {"text": "Black"},
                "result": json.dumps(
                    {
                        "success": True,
                        "action_taken": "selected_choice_text_Black",
                    }
                ),
            },
        ],
        "decisions": [
            {
                "index": 0,
                "snapshotIndex": 0,
                "player": "Alice",
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "actionType": "GAME_CHOOSE_CHOICE",
                "responseType": "index",
                "message": "Choose color",
                "choices": [],
                "choiceCount": 0,
                "isForced": True,
                "chosenArgs": {"choice": "Black"},
                "actionResult": {"error": "Unknown short ID: Black"},
                "llmEventIndices": [0, 1, 2],
                "subsequentActions": [],
            }
        ],
    }
    path.write_text(json.dumps(payload))

    status, count = backfill_game(path, force=True)

    assert status == "updated"
    assert count == 1
    updated = loads_json5(path.read_text())
    assert updated["decisions"][0]["chosenArgs"] == {"text": "Black"}
    assert updated["decisions"][0]["actionResult"] == {
        "success": True,
        "action_taken": "selected_choice_text_Black",
    }


def test_load_raw_game_export_handles_json_and_gz(tmp_path: Path) -> None:
    payload = _make_stub_export()
    json_path = tmp_path / "game_test.json5"
    gz_path = tmp_path / "game_test_copy.json5.gz"
    json_text = json.dumps(payload)

    json_path.write_text(json_text)
    gz_path.write_bytes(gzip.compress(json_text.encode()))

    assert load_raw_game_export(json_path)["id"] == payload["id"]
    assert load_raw_game_export(gz_path)["id"] == payload["id"]


def test_write_raw_game_export_switches_to_json_and_removes_gz(tmp_path: Path) -> None:
    payload = _make_stub_export("game_small")
    gz_path = tmp_path / "game_small.json5.gz"
    json_path = tmp_path / "game_small.json5"
    gz_path.write_bytes(b"stale")

    with patch("scripts.game_exports.GAME_EXPORT_GZ_THRESHOLD", 10_000):
        out_path = write_raw_game_export(gz_path, payload)

    assert out_path == json_path
    assert json_path.exists()
    assert not gz_path.exists()
    assert loads_json5(json_path.read_text())["id"] == payload["id"]


def test_write_raw_game_export_switches_to_gz_and_removes_json(tmp_path: Path) -> None:
    payload = _make_stub_export("game_large")
    json_path = tmp_path / "game_large.json5"
    gz_path = tmp_path / "game_large.json5.gz"
    json_path.write_text("stale")

    with patch("scripts.game_exports.GAME_EXPORT_GZ_THRESHOLD", 1):
        out_path = write_raw_game_export(json_path, payload)

    assert out_path == gz_path
    assert gz_path.exists()
    assert not json_path.exists()
    raw = gzip.decompress(gz_path.read_bytes())
    assert loads_json5(raw.decode())["id"] == payload["id"]


def test_write_raw_game_export_serializes_decision_support_dataclasses(
    tmp_path: Path,
) -> None:
    payload = {
        "version": 8,
        "id": "game_dataclass",
        "decisions": [
            {
                "index": 0,
                "snapshotIndex": 0,
                "player": "Alice",
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "actionType": "play",
                "responseType": "choice",
                "message": "Play spells and abilities",
                "choices": [Choice.from_mapping({"index": 0, "name": "Memnite", "power": "1"})],
                "choiceCount": 1,
                "isForced": True,
                "llmEventIndices": [],
                "subsequentActions": [],
                "pilotContext": PilotContext.from_mapping(
                    {"untappedLands": 1, "combatPhase": None, "manaPool": {"WHITE": 1}}
                ),
                "items": [MultiAmountItem.from_mapping({"description": "Assign damage", "target": "p1"})],
            }
        ],
    }

    out_path = write_raw_game_export(tmp_path / "game_dataclass.json5", payload)

    written = loads_json5(out_path.read_text())
    assert written["decisions"][0]["choices"][0] == {
        "index": 0,
        "name": "Memnite",
        "power": "1",
    }
    assert written["decisions"][0]["pilotContext"] == {
        "untappedLands": 1,
        "combatPhase": None,
        "manaPool": {"WHITE": 1},
    }
    assert written["decisions"][0]["items"][0] == {
        "description": "Assign damage",
        "target": "p1",
    }


def test_write_raw_game_export_serializes_action_from_as_json_from(
    tmp_path: Path,
) -> None:
    payload = {
        "id": "game_test_001",
        "actions": [
            Action(seq=1, type="chat", message="hello", from_="Alice"),
        ],
    }

    out_path = write_raw_game_export(tmp_path / "game_test_001.json5", payload)

    written = loads_json5(out_path.read_text())
    assert written["actions"][0]["from"] == "Alice"
    assert "from_" not in written["actions"][0]


def test_glob_game_export_paths_prefers_gz_when_both_exist(tmp_path: Path) -> None:
    (tmp_path / "game_a.json5").write_text("{}")
    (tmp_path / "game_b.json5.gz").write_bytes(gzip.compress(b"{}"))
    (tmp_path / "game_c.json5").write_text("{}")
    (tmp_path / "game_c.json5.gz").write_bytes(gzip.compress(b"{}"))

    paths = glob_game_export_paths(tmp_path)

    assert [path.name for path in paths] == [
        "game_a.json5",
        "game_b.json5.gz",
        "game_c.json5.gz",
    ]
