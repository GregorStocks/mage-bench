#!/usr/bin/env python3
"""Dump a sample blunder analysis prompt to tmp/sample_prompt.txt for spot-checking."""

from pathlib import Path

from schemas.game_export_types import load_game_export
from scripts.analysis.blunder_analysis import (
    _actions_by_turn,
    _collect_card_names,
    _game_overview,
    _get_oracle_texts,
    build_decision_prompt,
)
from scripts.analysis.extract_decisions import extract_decisions

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
TMP_DIR = REPO_ROOT / "tmp"

# Pick a game with enough turns
gz_path = str(REPO_ROOT / "website/public/games/game_20260216_155314_g7.json.gz")

data = load_game_export(gz_path)

decisions = extract_decisions(gz_path)
non_forced = [d for d in decisions if not d["is_forced"]]

# Pick a mid-game decision (around the middle of the game)
mid_idx = len(non_forced) // 2
decision = non_forced[mid_idx]

overview = _game_overview(data)
card_names = _collect_card_names(data)
oracle_texts = _get_oracle_texts(sorted(card_names))

game_actions = data.get("actions", [])
abt = _actions_by_turn(game_actions)
game_snapshots = data.get("snapshots", [])
num_players = len(data.get("players", []))

system_prompt, user_msg = build_decision_prompt(
    overview=overview,
    decision=decision,
    oracle_texts=oracle_texts,
    snapshots=game_snapshots,
    actions_by_turn=abt,
    num_players=num_players,
    all_actions=game_actions,
)

output = (
    f"=== SYSTEM PROMPT ===\n\n{system_prompt}\n\n=== USER MESSAGE ===\n\n{user_msg}"
)

out_path = TMP_DIR / "sample_prompt.txt"
out_path.write_text(output)

# Stats
sys_tokens = len(system_prompt) // 4
user_tokens = len(user_msg) // 4
print(f"Game: {data['id']}")
message = decision.get("message", "")
assert isinstance(message, str), f"decision message must be a string, got {message!r}"
print(
    f"Decision {decision['decision_index']}, turn {decision.get('turn')}, {decision['player']}"
)
print(f"Message: {message[:80]}")
print(f"\nSystem prompt: ~{sys_tokens} tokens")
print(f"User message:  ~{user_tokens} tokens")
print(f"\nWritten to: {out_path}")
