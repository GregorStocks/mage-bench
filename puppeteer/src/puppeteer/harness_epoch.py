"""Harness epoch tracking for game result comparability.

The harness epoch is a monotonic integer that marks breaking changes to the
evaluation harness (MCP tools, pilot logic, priority semantics). Games from
different epochs aren't directly comparable, so the leaderboard filters to
games at or above MIN_LEADERBOARD_EPOCH.
"""

# Current harness epoch. Bump when MCP tools, pilot logic, or priority
# semantics change enough to make game results non-comparable.
#
# History:
#   1 - Foundation: basic priority passing (Feb 10)
#   2 - yield_until + mana sourcing + error codes (Feb 12)
#   3 - Priority blocking + simplified pass_priority API (Feb 14)
#   4 - Short object IDs + batch combat (Feb 14)
#   5 - Fix parallel-game duplicate username disconnect loop (Feb 14)
#   6 - Bump max_tokens to 20k, fix GPT-5 Nano reasoning effort (Feb 14)
#   7 - Strip HTML tags and [xxx] hex ID suffixes from MCP tool results (Feb 15)
#   8 - pass_priority returns action choices; Anthropic prompt caching (Feb 15)
#   9 - GAME_CHOOSE_ABILITY presented to LLM instead of auto-selecting (Feb 15)
#  10 - Flat mana_plan format + multi-ability land support (Feb 15)
#  11 - Remove JsonArray from tool type system; blockers now "blocker:attacker" strings (Feb 15)
#  12 - Enrich get_oracle_text with mana_cost, type, P/T, loyalty, defense, second_face (Feb 16)
#  13 - pass_priority handles pending actions from choose_action instead of returning immediately (Feb 16)
#  14 - Oracle text (rules) in hand cards and mulligan context; remove land_count/hand_size from mull (Feb 17)
#  15 - Increase LLM request timeout from 45s to 120s (Feb 17)
#  16 - Remove unseenChat cap; add [System] Spell cancelled to pool mana cancel path (Feb 17)
#  17 - mana_plan exhaustion falls through to auto-tap instead of cancelling; improved mana docs (Feb 18)
#  18 - Capture cached_tokens and reasoning_tokens from LLM API responses (Feb 18)
#  19 - Surface modified permanent info: rules, original_card, copy for copies/transforms/granted abilities (Feb 18)
#  20 - Oracle text (rules) for all battlefield/graveyard/exile cards in get_game_state (Feb 19)
#  21 - Remove actions_passed from tool results; land_drops_used from server game view (Feb 24)
#  22 - Fix winsNeeded=2 causing double games; single-game matches only (Feb 25)
#  23 - Prompt cache breakpoints for Anthropic models (state bridge + tail) (Feb 25)
HARNESS_EPOCH = 23

# Minimum epoch for leaderboard inclusion. Games below this are shown
# in the games list but excluded from ELO ratings.
MIN_LEADERBOARD_EPOCH = 3

# Minimum blunder analysis version for "acceptable" annotations. Games
# analyzed below this show an "(older analysis)" tag on the website.
# (See BLUNDER_SCRIPT_VERSION in scripts/analysis/blunder_analysis.py.)
MIN_BLUNDER_VERSION = 11
