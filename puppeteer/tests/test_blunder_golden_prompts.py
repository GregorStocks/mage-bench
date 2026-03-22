"""Golden prompt tests for blunder annotator prompt assembly.

These are pure unit tests -- no XMage server, no API keys, no network calls.
They verify that the exact system+user message sent to Opus for blunder
evaluation matches golden reference files, catching regressions in prompt
assembly (format, context, card references).

To update golden files after intentional changes:
    make regen-golden
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from magebench.common.json5_utils import loads_json5
from magebench.common.json5_writer import dumps_json5
from scripts.analysis.blunder_analysis import (
    build_decision_prompt,
    load_game_context,
)
from scripts.analysis.blunder_eval_common import decision_index as get_decision_index

GOLDEN_DIR = Path(__file__).parent / "golden" / "blunder_prompts" / "game_20260216_074122_g2"
_GAMES_DIR = Path(__file__).resolve().parent.parent.parent / "website" / "public" / "games"
GAME_PATH = _GAMES_DIR / "game_20260216_074122_g2.json5.gz"
if not GAME_PATH.exists():
    GAME_PATH = _GAMES_DIR / "game_20260216_074122_g2.json5"
ORACLE_CACHE = GOLDEN_DIR / "oracle_cache.json5"

UPDATE_MODE = bool(os.environ.get("UPDATE_BLUNDER_GOLDEN"))

# Decision indices covering different game phases and context levels:
#   0   - Starting player selection (minimal prompt, no preceding action)
#   11  - Turn 1 postcombat main (early game, card ref, has preceding action)
#   64  - Turn 6 precombat main (mid-game, has prior context)
#   113 - Turn 8 end step (discard decision, 7 choices)
#   232 - Turn 18 precombat main (late game, max prior context)
GOLDEN_DECISION_INDICES = [0, 11, 64, 113, 232]


@pytest.fixture(scope="module")
def game_context():
    """Load game context via the production code path, with cached oracle texts."""
    oracle_texts = loads_json5(ORACLE_CACHE.read_text())
    with patch(
        "scripts.analysis.blunder_analysis.get_oracle_texts",
        return_value=oracle_texts,
    ):
        return load_game_context(str(GAME_PATH))


@pytest.mark.parametrize("decision_index", GOLDEN_DECISION_INDICES)
def test_blunder_prompt_golden(game_context, decision_index):
    """Verify blunder annotator prompt matches golden reference."""
    golden_path = GOLDEN_DIR / f"decision_{decision_index}.json5"

    decisions_by_index = {get_decision_index(d): d for d in game_context["decisions"]}
    decision = decisions_by_index[decision_index]
    preceding = game_context["preceding_by_index"].get(decision_index)

    system, user = build_decision_prompt(
        overview=game_context["overview"],
        decision=decision,
        oracle_texts=game_context["oracle_texts"],
        snapshots=game_context["snapshots"],
        actions_by_turn=game_context["actions_by_turn"],
        num_players=game_context["num_players"],
        all_actions=game_context["all_actions"],
        preceding_decision=preceding,
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
        golden_path.write_text(dumps_json5(actual) + "\n")
        return

    assert golden_path.exists(), (
        f"Golden file missing: {golden_path}\nRun UPDATE_BLUNDER_GOLDEN=1 make test to generate."
    )
    expected = loads_json5(golden_path.read_text())

    assert actual["system"] == expected["system"], "System prompt changed"
    assert actual["user"] == expected["user"], "User message changed"
