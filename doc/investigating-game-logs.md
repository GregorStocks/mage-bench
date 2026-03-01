# Investigating Game Logs

Useful tricks for analyzing game logs, discovered during analysis runs. This file is incrementally built up — each `analyze-game` run may add new techniques.

See `logging.md` for the file format reference.

## Finding game directories

```bash
# Most recent LLM game
GAME_DIR=~/.mage-bench/logs/$(readlink ~/.mage-bench/logs/last-llm4)

# Most recent game by config name
GAME_DIR=~/.mage-bench/logs/$(readlink ~/.mage-bench/logs/last-round-robin-1v1)

# Most recent game on a branch
GAME_DIR=~/.mage-bench/logs/$(readlink ~/.mage-bench/logs/last-branch-GregorStocks-my-branch)

# All recent games, newest first
ls -dt ~/.mage-bench/logs/game_* | head -5
```

## Chat messages

Chat messages are the most human-readable signal. Always check them first.

```bash
# Extract all chat messages with timestamps
jq -r 'select(.type=="player_chat") | "\(.timestamp) \(.message)"' "$GAME_DIR/game_events.jsonl"
```

## Error logs

```bash
# Show all errors across all players
for f in "$GAME_DIR"/*_errors.log; do echo "=== $(basename "$f") ==="; cat "$f"; done

# Count errors per player
wc -l "$GAME_DIR"/*_errors.log
```

## Stall and loop detection

```bash
# Find stall events
grep -n "Stalled:" "$GAME_DIR"/*_pilot.log

# Find auto-pass triggers
grep -n "auto-pass\|Brain freeze\|auto_pilot_mode" "$GAME_DIR"/*_pilot.log

# Count tool calls in bridge log to spot loops (top 5 most-called tools)
jq -r 'select(.type=="tool_call") | .tool' "$GAME_DIR"/*_bridge.jsonl | sort | uniq -c | sort -rn | head -5
```

## Server connection issues

```bash
# Check for socket/connection failures
grep -i "unable to create socket\|SESSION CALLBACK EXCEPTION\|waitResponseOpen\|disconnected" "$GAME_DIR/server.log"
```

## Game duration and flow

```bash
# First and last event timestamps (game duration)
head -1 "$GAME_DIR/game_events.jsonl" | jq -r .timestamp
tail -1 "$GAME_DIR/game_events.jsonl" | jq -r .timestamp

# Turn count
jq -r 'select(.type=="turn") | "\(.timestamp) Turn \(.turn_number) - \(.active_player)"' "$GAME_DIR/game_events.jsonl" | tail -5
```

## LLM cost analysis

```bash
# Cost per player
for f in "$GAME_DIR"/*_cost.json; do echo "$(basename "$f" _cost.json): $(cat "$f")"; done
```

## Blocking and combat issues

```bash
# Find empty-choices GAME_TARGET events (e.g. blocker assignment bugs)
grep -c "Select attacker to block" "$GAME_DIR"/*_pilot.log

# Look for repeated GAME_TARGET patterns in LLM logs
jq -r 'select(.type=="tool_result") | select(.result | contains("Select attacker to block"))' "$GAME_DIR"/*_llm.jsonl | head -5
```

## Priority desync detection

When a player is desynced, `pass_priority` returns timeout but the game is progressing for others.

```bash
# Count consecutive pass_priority timeouts (desync signature)
jq -r 'select(.type=="tool_result") | select(.result | contains("timeout")) | .ts' "$GAME_DIR"/*_llm.jsonl | head -20

# Cross-reference: does get_game_state show game progressing while pass_priority times out?
# Look for changing turn numbers in game_state responses during timeout periods
jq -r 'select(.type=="tool_result") | select(.tool=="get_game_state") | .result' "$GAME_DIR"/*_llm.jsonl | jq -r '.turn' | uniq -c
```

## Context trimming pressure

```bash
# Count context_trim events per player (high counts = LLM was looping)
jq -r 'select(.type=="context_trim")' "$GAME_DIR"/*_llm.jsonl | wc -l

# Check rendered_size after trims (should be ~62 when trimming is active)
jq -r 'select(.type=="context_trim") | .rendered_size' "$GAME_DIR"/*_llm.jsonl | sort -n | uniq -c
```

## Mana payment errors

```bash
# Find GAME_CHOOSE_ABILITY errors (dual land mana selection)
grep "GAME_CHOOSE_ABILITY" "$GAME_DIR"/*_errors.log

# Find GAME_PLAY_MANA answer=true rejections
grep "choose mana source/pool" "$GAME_DIR"/*_errors.log

# Detect silent spell cancellation from partial auto-mana payment
# When auto-mana pays part of the cost but can't complete, the spell is silently cancelled.
# The LLM may hallucinate that the spell resolved. Cross-reference:
# 1. Find the partial payment failure
grep "no auto source available" "$GAME_DIR"/*_pilot.log
# 2. Check if the spell is still in hand after the "cast" (compare hand before/after in bridge log)
jq -r 'select(.method=="GAME_UPDATE" or .method=="GAME_UPDATE_AND_INFORM") | "\(.ts) \(.data)"' "$GAME_DIR"/*_bridge.jsonl | grep -A1 "GAME_PLAY_MANA"
```

## Short ID crashes and remapping

```bash
# Find ShortIdRegistry crashes (mana_plan referencing remapped IDs)
grep "Unknown short ID" "$GAME_DIR"/*_mcp.log

# Find GAME_TARGET ID mismatches (model sends valid-looking IDs that don't resolve)
grep "not found in current choices" "$GAME_DIR"/*_errors.log

# Cross-reference: what IDs were in the choices vs what the model sent
# Look for GAME_TARGET choices lists immediately before the error
grep -B5 "not found in current choices" "$GAME_DIR"/*_pilot.log
```

These often indicate ShortIdRegistry remapping: a card's short ID changed between
when choices were presented and when the LLM responded. Common with multi-step
targeting (e.g., Doomsday pile construction) and mana sources that get sacrificed.

## Ward / additional cost prompt confusion

When a model casts a spell targeting a creature with ward, the game sends a
GAME_ASK to the caster asking whether they want to pay the additional cost.
If the model skips `get_action_choices` and guesses what the GAME_ASK is about,
it may answer `false` (decline to pay), countering its own spell.

```bash
# Find ward-related GAME_ASK decisions in bridge logs
grep -A1 "GAME_ASK" "$GAME_DIR"/*_bridge.jsonl | grep -i "ward\|pay\|counter"

# Check if a model declined to pay its own ward cost
grep "chooses not to pay" "$GAME_DIR"/*_bridge.jsonl
```

Cross-reference with the LLM trace: look for `choose_action(answer=false)`
immediately after a `next_action_type: "GAME_ASK"` with no intervening
`get_action_choices` call — the model never saw the question text.

## Tool name formatting issues

Some models (notably Kimi K2.5) emit tool names with leading whitespace, causing
`Unknown tool` errors. The MCP registry does exact string matching.

```bash
# Find tool name whitespace errors (model-specific formatting bugs)
grep "Unknown tool:" "$GAME_DIR"/*_errors.log

# Count per model to identify systematic issues
grep -c "Unknown tool:" "$GAME_DIR"/*_errors.log
```

## Verifying blunder annotations against decisions

Batch attack/block decisions have `chosen=None` (the actual data is in `chosenArgs`).
The blunder LLM may misinterpret these as timeouts. Verify annotations against decisions:

```python
# Check which batch decisions generated false-positive annotations
import json
from scripts.analysis.blunder_eval_common import compute_aftermath_index

with open('website/public/games/GAME_ID.json') as f:
    data = json.load(f)

for d in data['decisions']:
    if d.get('chosen') is None and d.get('chosenArgs', {}).get('attackers'):
        aft = compute_aftermath_index(d, data['snapshots'])
        has_ann = any(a['snapshotIndex'] == aft for a in data.get('annotations', []))
        if has_ann:
            print(f"Decision {d['index']}: batch_attack {d['chosenArgs']} -> FALSE POSITIVE annotation at snapshot {aft}")
```

## Tracing auto-mana payment sequences

```bash
# Show GAME_PLAY_MANA callbacks with timestamps (auto-handled ones are ~100ms apart)
jq -r 'select(.method=="GAME_PLAY_MANA") | .ts' "$GAME_DIR"/*_bridge.jsonl

# Cross-reference with pilot log to see if LLM saw the mana prompt
# If the pilot log shows no GAME_PLAY_MANA interaction between the cast and pass_priority,
# auto-mana handled it silently (or the pending action was consumed by pass_priority)
grep -n "GAME_PLAY_MANA\|pass_priority\|no auto source" "$GAME_DIR"/*_pilot.log
```
