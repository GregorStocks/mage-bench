"""Harness epoch tracking for game result comparability.

The harness epoch is a monotonic integer that marks breaking changes to the
evaluation harness (MCP tools, pilot logic, priority semantics).

SEASON_1_START_EPOCH is the first epoch that counts as season 1. Used at
export time to assign the season field for games that predate run-time season
tracking. The leaderboard and matchmaker filter by the season field, not by
epoch directly.
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
#  24 - Add source_card to stack abilities for website viewer art display (Feb 26)
#  25 - Render pass_priority/get_action_choices as structured text for LLM (Feb 27)
#  26 - Enrich rendered board: ## headings, loyalty/token/copy on permanents, PREGAME phase (Feb 27)
#  27 - Server-assigned player + lookedAt short IDs (unified p-prefix namespace) (Feb 28)
#  28 - Fix blocker format: system prompt + respond_with now teach "p5:p1" strings; trim MCP tool names (Feb 28)
#  29 - Chat nudge: system prompt encourages chatting; pilot injects reminders when silent 2+ turns (Feb 28)
#  30 - Include next_action_message in choose_action response when next_action_pending=true (Feb 28)
#  31 - Text-based invalid_choice errors now include valid choices in response (Feb 28)
#  32 - Fix client-side yield cancelling pending non-priority actions (GAME_TARGET, etc.) (Feb 28)
#  33 - Fix chosen field in exports: prefer id over index when both present (Mar 1)
#  34 - Fix stack_resolved fast-path bypassing non-priority action guard (Mar 1)
#  35 - Server-assigned short IDs for callback cards (scry, tutor, pile choices) (Mar 1)
#  36 - Simplify choose_action: merge index/id/answer into choice; arrays to comma-separated strings (Mar 1)
#  37 - Shorten MCP tool/param/output descriptions for token efficiency (Mar 1)
#  38 - Remove bridge-side stall recovery (lost response retry + speculative pass) (Mar 2)
#  39 - Add retry limit for empty LLM choices; auto-pass after 5 consecutive (Mar 3)
#  40 - Move action-type docs from system prompt into respond_with strings (Mar 3)
#  41 - Replace Map<String, Object> tool results with typed Result classes (Mar 4)
#  42 - Rewrite system prompt game flow: startup/mulligan/main loop phases; get_action_choices first (Mar 5)
#  43 - Fix end_of_turn yield persisting across turns when server skips END_TURN callbacks (Mar 5)
#  44 - Oracle text dedup: Card Reference only shows each card's oracle text once per conversation (Mar 5)
#  45 - Write season to game_meta.json at run time (Mar 6)
#  46 - Redefine stack_resolved to watch the current stack object and only wake on a later actionable callback (Mar 14)
#  47 - Richer stack ability summaries in bridge tool results and rendered prompts (Mar 14)
#  48 - State-bridge prompt teaches choose_action(choice=no) instead of answer=false (Mar 14)
#  49 - Strip synthetic AbilityPicker ordinals from GAME_CHOOSE_ABILITY labels (Mar 14)
#  50 - Stop step yields on same-turn overshoot and any later-turn callback (Mar 15)
#  51 - Render multi-amount item descriptions in pilot prompts (Mar 15)
#  52 - Surface MultiAmountType header as message for GAME_GET_MULTI_AMOUNT decisions (Mar 15)
#  53 - Simplify multi-amount total display: total=N when min==max; consistent respond text (Mar 15)
#  54 - Stop passive GAME_UPDATE state mutation; stack owner from CardView.controllerId (Mar 15)
HARNESS_EPOCH = 54

# First epoch that counts as season 1. Used only for games that predate
# run-time season tracking (no "season" in game_meta.json). Export and
# schema migrations use this to assign season 0 vs 1 for old games.
SEASON_1_START_EPOCH = 11

# Minimum blunder analysis version for "acceptable" annotations. Games
# analyzed below this show an "(older analysis)" tag on the website.
# (See BLUNDER_SCRIPT_VERSION in scripts/analysis/blunder_analysis.py.)
MIN_BLUNDER_VERSION = 11
