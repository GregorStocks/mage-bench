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

## Bootstrap caveats

`game-gz-bootstrap.py` is still useful for a quick overview, but its failure count
is currently only advisory:

- it counts any tool result containing the substring `required` as a failed call,
  so mandatory prompts can show up as bogus failures
- its auto-export fallback still looks under `~/mage-bench-logs` instead of
  `~/.mage-bench/logs`

Sanity-check suspicious bootstrap output against the export before treating it as
real runtime evidence:

```bash
# Compare bootstrap "failed tool calls" against the export summary
jq '.players[] | {name, toolCallsFailed}' website/public/games/GAME_ID.json

# If the export is missing, generate it manually from the real log root
uv run python scripts/export_game.py GAME_ID
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

## Hallucinated object IDs

When models fabricate IDs that don't exist in the game state (common with gpt-4o-mini
and other weak models that skip `get_action_choices`):

```bash
# Detect hallucinated or stale IDs in blocker/attacker assignments and targeting
grep "unknown attacker ID\|not a valid block target\|not found in current choices" "$GAME_DIR"/*_mcp.log "$GAME_DIR"/*_pilot.log
```

If the IDs look systematically wrong rather than random, compare the exact
rendered prompt in `*_llm_trace.jsonl` with the structured MCP/export payload.
That distinguishes model hallucination from a renderer omission:

```bash
# What text did the model actually see?
rg -n "Turn [0-9]+ COMBAT|Combat:" "$GAME_DIR"/*_llm_trace.jsonl

# Does the structured export/MCP data have fields that the rendered prompt omitted?
rg -n '"incoming_attackers"|Combat Phase: blockers' website/public/games/GAME_ID.json "$GAME_DIR"/*_llm.jsonl
```

If the JSON has `incoming_attackers` or other blocker metadata but the prompt
only shows attacker names, suspect `decision_renderer.py` instead of the model.

## Generic `Ability -> player` stack summaries

When a triggered ability prompt degrades to something like
`Stack: [Ability -> {'name': 'Player (you)', 'id': 'p2'}]`, compare the prompt
stack summary against the export snapshot before blaming the model.

```bash
# What stack text did the model actually see?
rg -n "Stack: \\[Ability" "$GAME_DIR"/*_llm_trace.jsonl

# Does the export preserve the missing source card / ability text?
rg -n '"source_card"|"ability_text"|stack ability \\(' website/public/games/GAME_ID.json
```

If the export has `source_card` / `ability_text` but `pass_priority` or
`get_action_choices` only expose `name: "Ability"` plus raw target dicts, the
bug is in bridge/prompt serialization (`BridgeCallbackHandler`,
`decision_renderer.py`), not in XMage state collection.

## Blind index-0 targeting

Weak models skip `get_action_choices` and default to `index:0` for targeting,
often hitting the wrong creature (opponent's creatures may be listed first).

```bash
# Count instances where model skipped get_action_choices
grep -c "auto-populating choices" "$GAME_DIR"/*_mcp.log
```

Cross-reference with blunder annotations to see if blind targeting caused misplays.

## GAME_CHOOSE_CHOICE false positives in blunder annotations

Like batch attack/block decisions, `GAME_CHOOSE_CHOICE` decisions use `chosenArgs.text`
instead of the `chosen` field. The blunder LLM may interpret `chosen=None` as a timeout
when the model actually selected via text:

```python
# Find GAME_CHOOSE_CHOICE decisions that might generate false annotations
import json
with open('website/public/games/GAME_ID.json') as f:
    data = json.load(f)

for d in data['decisions']:
    if d.get('actionType') == 'GAME_CHOOSE_CHOICE' and d.get('chosen') is None:
        text = d.get('chosenArgs', {}).get('text', '')
        if text:
            has_ann = any(a['snapshotIndex'] == d.get('snapshotIndex') for a in data.get('annotations', []))
            print(f"Decision {d['index']}: GAME_CHOOSE_CHOICE text='{text}' chosen=None {'<-- FALSE POSITIVE annotation' if has_ann else ''}")
```

## Exported decisions can stop at the first failed `choose_action`

If a model retries the same pending action after an error, `scripts/export_game.py`
can record the first failed `choose_action` as the decision and split the later
successful retry into a blank follow-up decision. Symptoms:

- decision N has `actionResult.error`
- decision N+1 for the same player/snapshot has empty `chosenArgs` / `actionResult`
- the board in decision N+1 already reflects the successful retry
- an annotation claims timeout/default behavior that contradicts the raw logs

```bash
# Find suspicious adjacent decisions after a failed retry
jq '.decisions[] | {index, player, snapshotIndex, actionType, message, chosenArgs, actionResult}' \
  website/public/games/GAME_ID.json | less
```

Then confirm in the raw LLM log that the same pending action had a later successful retry:

```bash
nl -ba ~/.mage-bench/logs/GAME_ID/*_llm.jsonl | grep -n "selected_choice_text_\|Unknown short ID\|invalid_choice"
```

If the later raw log succeeds but the export still shows a failed choice plus a blank
next decision, trust the raw `*_llm.jsonl` and inspect `scripts/export_game.py` /
`scripts/analysis/extract_decisions.py` before trusting annotations.

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

## `game_timeline.py --turns` caveat

Some v7 exports omit `snapshots[].ts`. When that happens,
`scripts/analysis/game_timeline.py --turns ...` silently maps every `llmEvent`
to the final turn because it falls back to `""` for missing snapshot timestamps.
Sanity-check the export before trusting turn-range filters:

```bash
python - <<'PY'
import json
from pathlib import Path

data = json.loads(Path("website/public/games/GAME_ID.json").read_text())
print("snapshot_has_ts =", bool(data.get("snapshots")) and "ts" in data["snapshots"][0])
PY
```

If that prints `False`, avoid `--turns` for now and use the full timeline plus
`--player`, or inspect `llmEvents` / bridge logs directly.

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

## Verifying `chosen` field accuracy (id vs index conflicts)

When a model sends both `id` and `index` in `choose_action`, the bridge prefers `id`
but the decisions export's `chosen` field may reflect the raw `index`. This can cause
false blunder annotations (e.g., "targeted self" when the target was actually the opponent).

```python
# Find decisions where action_taken disagrees with chosen
import json
with open('website/public/games/GAME_ID.json') as f:
    data = json.load(f)

for d in data['decisions']:
    chosen = d.get('chosen')
    taken = d.get('actionResult', {}).get('action_taken', '')
    if isinstance(chosen, int) and taken.startswith('selected_target_'):
        actual = int(taken.split('_')[-1])
        if chosen != actual:
            print(f"Decision {d['index']}: chosen={chosen} but action_taken={taken}")
            print(f"  choices[{chosen}]={d['choices'][chosen].get('name')}")
            print(f"  choices[{actual}]={d['choices'][actual].get('name')}")
```

Also check `actionResult.warning` for "Both id and index provided" messages.

## Tracing auto-mana payment sequences

```bash
# Show GAME_PLAY_MANA callbacks with timestamps (auto-handled ones are ~100ms apart)
jq -r 'select(.method=="GAME_PLAY_MANA") | .ts' "$GAME_DIR"/*_bridge.jsonl

# Cross-reference with pilot log to see if LLM saw the mana prompt
# If the pilot log shows no GAME_PLAY_MANA interaction between the cast and pass_priority,
# auto-mana handled it silently (or the pending action was consumed by pass_priority)
grep -n "GAME_PLAY_MANA\|pass_priority\|no auto source" "$GAME_DIR"/*_pilot.log
```

## Personality infection and chat spam

Expressive personalities (valley-girl, dramatist, villain) can cause models to spend
enormous reasoning tokens on in-character chat narration instead of game analysis.
Symptoms: timer timeout despite winning board position, context overflow, cascading
LLM empty responses after context trim.

```bash
# Count chat messages per player (>20 is suspicious, >50 is severe)
jq -r 'select(.type=="player_chat") | .player' "$GAME_DIR/game_events.jsonl" | sort | uniq -c | sort -rn

# Count send_chat_message calls in pilot logs
grep -c "send_chat_message" "$GAME_DIR"/*_pilot.log

# Check if chat messages narrate failed plays (personality running ahead of game state)
# Look for send_chat_message immediately after "Action failed" warnings
grep -A2 "Action failed" "$GAME_DIR"/*_pilot.log | grep "send_chat_message"
```

Cross-reference with LLM timeout/empty-response patterns — chat spam often precedes
context exhaustion. Check the personality in game_meta.json to identify infection-prone
personalities vs clean ones (analyst, spike, stoic are generally safe).

## Detecting hallucinated action batching

Some models (observed with gpt-oss-120b) send multi-tool-call LLM responses where
they plan an entire turn sequence from imagined game state rather than reading actual
choices after each action. When any action fails (stale ID, state changed), the
entire batch breaks. Symptoms: many `invalid_choice` errors, thinking traces that
describe plays that never happened, pass_priority calls after "completing" imaginary
actions.

```bash
# Count tool calls per LLM response (>5 in a single response = likely batching)
jq -r 'select(.type=="llm_response") | [.tool_calls[]?.name] | length' "$GAME_DIR"/*_llm.jsonl | sort -rn | head -5

# Cross-reference: check thinking traces for hallucinated plays
# Look for "I've played" / "I've cast" / "deployed" in thinking that precedes failed actions
grep -B2 "Action failed" "$GAME_DIR"/*_pilot.log | grep -i "played\|cast\|deployed"
```

## Output schema validation errors (OpenAI structured outputs)

When OpenAI models use structured outputs, tool results are validated against the output
schema. Schema mismatches cause the model to receive an error even though the action
succeeded on the game server.

```bash
# Find output schema validation errors in pilot logs
grep "Invalid structured content returned by tool" "$GAME_DIR"/*_pilot.log

# Count per tool (identify which tools have schema mismatches)
grep -o "Invalid structured content returned by tool [a-z_]*" "$GAME_DIR"/*_pilot.log | sort | uniq -c

# Check if the action actually succeeded despite the error
# Look for game state changes between the failed call and the next pass_priority
grep -A3 "Invalid structured content" "$GAME_DIR"/*_pilot.log | grep "Action:"
```

Previously seen schema mismatches (now fixed):

- `choose_action` `declared` field: was `List<Object>` which mapped to `items.type = "object"`,
  but batch attacks returned bare strings. Fixed by wrapping in `{"id": shortId}` objects.

## Batch combat empty error messages

When `handleBatchBlockers` or `handleBatchAttackers` partially fails (e.g. invalid blocker/attacker
ID), `result.failed` contains the reasons but `result.error` is null. The pilot logs
"Action failed: " (empty string) and the LLM can't understand what went wrong.

```bash
# Find batch combat failures with empty errors in LLM logs
jq -c 'select(.type=="tool_call") | select(.result | contains("batch_block") or contains("batch_attack")) | select(.result | fromjson | .success == false)' "$GAME_DIR"/*_llm.jsonl

# Check the failed array for actual reasons
jq -c 'select(.type=="tool_call") | select(.result | contains("batch_block") or contains("batch_attack")) | (.result | fromjson | {success, error, failed, declared})' "$GAME_DIR"/*_llm.jsonl
```

## Batch combat race conditions

When batch_block or batch_attack returns success but the game shows different results
(e.g., all attackers "unblocked" despite a block being declared), check for rapid
GAME_SELECT pairs in the bridge log — two GAME_SELECTs within ~200ms can indicate
the game state changed between prompt and response.

```bash
# Find rapid GAME_SELECT pairs in bridge logs (potential race conditions)
# Look for pairs within 500ms of each other
jq -r 'select(.method=="GAME_SELECT") | .ts' "$GAME_DIR"/*_bridge.jsonl | \
  awk -F'[T.]' '{print $2"."$3}' | \
  awk 'NR>1 {split(prev,a,":"); split($0,b,":"); diff=(b[1]-a[1])*3600+(b[2]-a[2])*60+(b[3]-a[3]); if(diff<0.5 && diff>0) print "Rapid pair: "prev" -> "$0" ("diff"s)"} {prev=$0}'

# Cross-reference: check if batch_block response time matches waitForNextCallback timeout (10s)
# A 10-second gap between choose_action and Action: batch_block in pilot logs = likely timeout
grep -A1 "choose_action.*blockers" "$GAME_DIR"/*_pilot.log
```

## Detecting client-side yield spell cancellation loops

When a model casts a targeted spell (e.g. Flames of the Firebrand) and then calls
`pass_priority(until="stack_resolved")` or `get_action_choices(until="stack_resolved")`,
the bridge cancels the pending GAME_TARGET. The spell fizzles and the card returns to hand.
The model then retries, creating a loop.

```python
# From the export: count decisions with chosen=None per turn (loop signature)
import json
with open('website/public/games/GAME_ID.json') as f:
    data = json.load(f)

from collections import Counter
for player in set(d['player'] for d in data['decisions']):
    none_by_turn = Counter()
    for d in data['decisions']:
        if d['player'] == player and d.get('chosen') is None:
            none_by_turn[d['turn']] += 1
    if any(v > 5 for v in none_by_turn.values()):
        print(f"{player} loop turns: {dict(t for t in none_by_turn.items() if t[1] > 5)}")
```

```python
# From the export: count until= usage per model (tool misuse signature)
from collections import Counter
for player_data in data.get('players', []):
    name = player_data['name']
    traces = [t for t in data['llmTrace'] if t.get('player') == name]
    until_counts = Counter()
    for t in traces:
        resp = t.get('response', {})
        for choices in resp.get('choices', []):
            for tc in choices.get('message', {}).get('tool_calls', []):
                args = json.loads(tc.get('function', {}).get('arguments', '{}'))
                if 'until' in args:
                    until_counts[tc['function']['name'] + '(until=' + args['until'] + ')'] += 1
    if until_counts:
        print(f"{name}: {dict(until_counts.most_common(5))}")
```

## Detecting stack_resolved hangs after passive resolution

If `pass_priority(until="stack_resolved")` returns only at `game_over`, check whether
the final stack item actually resolved on passive updates instead of a fresh `GAME_SELECT`.

```bash
# Find stack_resolved tool calls and their return latency
grep -n '"until":"stack_resolved"' "$GAME_DIR"/*_llm.jsonl

# Then inspect the bridge callbacks around the end of the sequence
nl -ba "$GAME_DIR"/*_bridge.jsonl | tail -n 40
```

Look for this signature:

- one last `GAME_SELECT` while the stack is still non-empty
- then only `GAME_UPDATE` / `GAME_UPDATE_AND_INFORM` callbacks
- `game_events.jsonl` or state snapshots showing `stack: []`
- `*_mcp.log` `passPriority STILL WAITING` lines with `pendingAction=false`

That pattern means the bridge auto-passed the last actionable priority window, the
stack emptied on passive callbacks, and the `stack_resolved` waiter never noticed
because it only checks stack emptiness while `pendingAction != null`.
