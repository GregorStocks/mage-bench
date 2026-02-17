#!/usr/bin/env python3
"""Dump a sample blunder analysis prompt to tmp/sample_prompt.txt for spot-checking."""

import gzip
import json
from pathlib import Path

from blunder_analysis import (
    PER_DECISION_FOOTER,
    PER_DECISION_SYSTEM,
    _actions_by_turn,
    _card_reference_for_decision,
    _collect_card_names,
    _format_decisions,
    _format_prior_context,
    _game_overview,
    _get_oracle_texts,
)
from extract_decisions import extract_decisions

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TMP_DIR = REPO_ROOT / "tmp"

# Pick a game with enough turns
gz_path = str(REPO_ROOT / "website/public/games/game_20260216_155314_g7.json.gz")

with gzip.open(gz_path, "rt") as f:
    data = json.load(f)

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

# Build the prompt exactly as sent to the LLM
formatted = _format_decisions([decision])
card_ref = _card_reference_for_decision(decision, oracle_texts)
num_players = len(data.get("players", []))
prior_ctx = _format_prior_context(decision, game_snapshots, abt, num_players)

user_msg = f"## Game Overview\n{overview}"
if card_ref:
    user_msg += f"\n\n{card_ref}"
if prior_ctx:
    user_msg += f"\n\n{prior_ctx}"
user_msg += f"\n\n## Decision\n\n{formatted}"
user_msg += f"\n\n{PER_DECISION_FOOTER}"

output = f"=== SYSTEM PROMPT ===\n\n{PER_DECISION_SYSTEM}\n\n=== USER MESSAGE ===\n\n{user_msg}"

out_path = TMP_DIR / "sample_prompt.txt"
out_path.write_text(output)

# Stats
sys_tokens = len(PER_DECISION_SYSTEM) // 4
user_tokens = len(user_msg) // 4
prior_tokens = len(prior_ctx) // 4 if prior_ctx else 0
print(f"Game: {data['id']}")
print(
    f"Decision {decision['decision_index']}, turn {decision.get('turn')}, {decision['player']}"
)
print(f"Message: {decision.get('message', '')[:80]}")
print(f"\nSystem prompt: ~{sys_tokens} tokens")
print(f"User message:  ~{user_tokens} tokens")
print(f"  of which prior context: ~{prior_tokens} tokens")
print(f"\nWritten to: {out_path}")
