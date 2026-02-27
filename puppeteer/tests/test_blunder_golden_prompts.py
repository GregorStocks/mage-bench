"""Golden prompt tests for blunder annotator prompt assembly.

These are pure unit tests -- no XMage server, no API keys, no network calls.
They verify that the exact system+user message sent to Opus for blunder
evaluation matches golden reference files, catching regressions in prompt
assembly (format, context, card references).

To update golden files after intentional changes:
    UPDATE_BLUNDER_GOLDEN=1 make test
    # or:
    make update-blunder-golden
"""

import gzip
import json
import os
from pathlib import Path

import pytest
from blunder_analysis import (
    _actions_by_turn,
    _game_overview,
    build_decision_prompt,
)
from extract_decisions import extract_decisions

GOLDEN_DIR = Path(__file__).parent / "golden" / "blunder_prompts" / "game_20260216_074122_g2"
GAME_PATH = (
    Path(__file__).resolve().parent.parent.parent / "website" / "public" / "games" / "game_20260216_074122_g2.json.gz"
)
ORACLE_CACHE = GOLDEN_DIR / "oracle_cache.json"

UPDATE_MODE = bool(os.environ.get("UPDATE_BLUNDER_GOLDEN"))

# Decision indices covering different game phases and context levels:
#   0   - Starting player selection (minimal prompt, no board/prior context)
#   11  - Turn 1 postcombat main (early game, card ref, no prior context)
#   64  - Turn 6 precombat main (mid-game, has prior context)
#   113 - Turn 8 end step (discard decision, 7 choices)
#   232 - Turn 18 precombat main (late game, max prior context)
GOLDEN_DECISION_INDICES = [0, 11, 64, 113, 232]


@pytest.fixture(scope="module")
def game_context():
    """Load game data and build context (once per module, no network calls)."""
    with gzip.open(str(GAME_PATH), "rt") as f:
        data = json.load(f)

    oracle_texts = json.loads(ORACLE_CACHE.read_text())
    decisions = extract_decisions(str(GAME_PATH))
    snapshots = data.get("snapshots", [])
    overview = _game_overview(data)
    game_actions = data.get("actions", [])
    abt = _actions_by_turn(game_actions)
    num_players = len(data.get("players", []))

    # Build index for quick lookup by decision_index
    by_index = {d["decision_index"]: d for d in decisions}

    return {
        "decisions_by_index": by_index,
        "snapshots": snapshots,
        "overview": overview,
        "oracle_texts": oracle_texts,
        "actions_by_turn": abt,
        "num_players": num_players,
        "all_actions": game_actions,
    }


@pytest.mark.parametrize("decision_index", GOLDEN_DECISION_INDICES)
def test_blunder_prompt_golden(game_context, decision_index):
    """Verify blunder annotator prompt matches golden reference."""
    golden_path = GOLDEN_DIR / f"decision_{decision_index}.json"

    decision = game_context["decisions_by_index"][decision_index]
    assert decision["decision_index"] == decision_index

    system, user = build_decision_prompt(
        overview=game_context["overview"],
        decision=decision,
        oracle_texts=game_context["oracle_texts"],
        snapshots=game_context["snapshots"],
        actions_by_turn=game_context["actions_by_turn"],
        num_players=game_context["num_players"],
        all_actions=game_context["all_actions"],
    )

    actual = {
        "decision_index": decision_index,
        "turn": decision.get("turn"),
        "phase": decision.get("phase"),
        "player": decision["player"],
        "message": decision.get("message", ""),
        "system": system,
        "user": user,
    }

    if UPDATE_MODE:
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(json.dumps(actual, indent=2) + "\n")
        return

    assert golden_path.exists(), (
        f"Golden file missing: {golden_path}\nRun UPDATE_BLUNDER_GOLDEN=1 make test to generate."
    )
    expected = json.loads(golden_path.read_text())

    assert actual["system"] == expected["system"], "System prompt changed"
    assert actual["user"] == expected["user"], "User message changed"
