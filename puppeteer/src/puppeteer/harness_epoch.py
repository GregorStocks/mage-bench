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
HARNESS_EPOCH = 12

# Minimum epoch for leaderboard inclusion. Games below this are shown
# in the games list but excluded from ELO ratings.
MIN_LEADERBOARD_EPOCH = 2
