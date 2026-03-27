#!/usr/bin/env python3
"""Dump a sample blunder analysis prompt to tmp/sample_prompt.txt for spot-checking."""

from pathlib import Path

from magebench.analysis.blunder.blunder_analysis import build_decision_prompt
from magebench.analysis.blunder.blunder_context import (
    actions_by_turn,
    collect_card_names,
    game_overview,
    get_oracle_texts,
)
from magebench.analysis.blunder.blunder_eval_common import load_game
from magebench.analysis.blunder.extract_decisions import extract_decisions
from magebench.game.game_exports import find_game_export_path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
TMP_DIR = REPO_ROOT / "tmp"

# Pick a game with enough turns
GAME_ID = "game_20260216_155314_g7"
game_path = find_game_export_path(GAME_ID)
assert game_path is not None, f"Game export not found for {GAME_ID}"

data = load_game(game_path)

decisions = extract_decisions(str(game_path))
non_forced = [d for d in decisions if not d.is_forced]

# Pick a mid-game decision (around the middle of the game)
mid_idx = len(non_forced) // 2
decision = non_forced[mid_idx]

overview = game_overview(data)
card_names = collect_card_names(data)
oracle_texts = get_oracle_texts(sorted(card_names))

game_actions = data.actions
abt = actions_by_turn(game_actions)
game_snapshots = data.snapshots
num_players = len(data.players)

system_prompt, user_msg = build_decision_prompt(
    overview=overview,
    decision=decision,
    oracle_texts=oracle_texts,
    snapshots=game_snapshots,
    actions_by_turn=abt,
    num_players=num_players,
    all_actions=game_actions,
)

output = f"=== SYSTEM PROMPT ===\n\n{system_prompt}\n\n=== USER MESSAGE ===\n\n{user_msg}"

out_path = TMP_DIR / "sample_prompt.txt"
out_path.write_text(output)

# Stats
sys_tokens = len(system_prompt) // 4
user_tokens = len(user_msg) // 4
print(f"Game: {data.id}")
print(f"Decision {decision.index}, turn {decision.turn}, {decision.player}")
print(f"Message: {decision.message[:80] if decision.message else ''}")
print(f"\nSystem prompt: ~{sys_tokens} tokens")
print(f"User message:  ~{user_tokens} tokens")
print(f"\nWritten to: {out_path}")
