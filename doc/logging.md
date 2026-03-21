# Logging

All game logs live in `~/.mage-bench/logs/game_YYYYMMDD_HHMMSS/`.

## Per-player log files

| File pattern | Source | Contents |
| --- | --- | --- |
| `{name}_pilot.log` | `pilot.py` stdout | LLM reasoning, tool calls, game actions, errors |
| `{name}_mcp.log` | Bridge JVM stdout (spawned by `pilot.py`, `sleepwalker.py`, or `replay.py`) | Bridge startup, MCP tool activity, auto-play actions, game log excerpts |
| `{name}_errors.log` | Python `_log_error()` + Java `logError()` | Errors only (written in real-time from both sides) |
| `{name}_cost.txt` | `llm_cost.py` | Cumulative LLM API cost in USD |

## Structured game log (JSONL)

Machine-readable JSONL files for post-game analysis. Each line is a compact JSON object with `ts`, `seq`, `type`, and event-specific fields.

| File | Source | Contents |
| --- | --- | --- |
| `game_meta.json` | `orchestrator.py` at game start | Decklists, models, system prompts, format, git info |
| `game_events.jsonl` | Spectator (`ObserverGamePanel`) | Game actions, player chat, state snapshots (all hands visible), game over |
| `{name}_llm.jsonl` | `pilot.py` per player | LLM reasoning, tool calls + results, costs, errors, stalls, context trims |
| `{name}_llm_trace.jsonl` | `pilot.py` per player | Full LLM request/response pairs (messages array + complete API response) |
| `{name}_bridge.jsonl` | `BridgeCallbackHandler` per player | Raw callback dump — every callback the bridge sees (data hoarding) |
| `game.jsonl` | `orchestrator.py` post-game merge | Unified log: `game_events.jsonl` + all `*_llm.jsonl` sorted by timestamp (excludes trace files) |

### Event types in game_events.jsonl

- `game_action` — game log message (e.g. "Gemini plays Mountain")
- `player_chat` — player chat message with `from` field
- `state_snapshot` — full game state: turn, phase, step, all players (life, hand cards, battlefield, graveyard, commanders, counters), stack
- `game_over` — game end message

### Event types in {name}_llm.jsonl

- `game_start` — model, system prompt, available tools, deck path
- `llm_response` — reasoning text, tool calls, token usage, cost, cumulative cost
- `tool_call` — tool name, arguments, full result, latency
- `context_trim` — messages before/after count
- `context_reset` — reason (e.g. "repeated_timeouts")
- `llm_error` — error type and message
- `stall` — turns without progress count
- `auto_pilot_mode` — reason for switching to auto-pass
- `game_end` — final cost

### Event types in {name}_llm_trace.jsonl

- `llm_call` — full LLM API request (`model`, `messages`, `tools`, `tool_choice`, `max_tokens`) and response (`choices`, `usage`, `id`, `model`). Each line is a self-contained, replayable LLM call. Not included in `game.jsonl` merge.

### Querying with jq

```bash
# All game actions
jq 'select(.type=="game_action")' game.jsonl

# LLM reasoning interleaved with game events
jq 'select(.type=="llm_response" or .type=="game_action")' game.jsonl

# State snapshots with all hands
jq 'select(.type=="state_snapshot") | .players[] | {name, life, hand}' game.jsonl

# Total LLM cost
jq 'select(.type=="game_end") | {player, total_cost_usd}' game.jsonl
```

## Aggregated files

| File | Created by | Contents |
| --- | --- | --- |
| `errors.log` | `orchestrator.py` `_write_error_log()` | All `*_errors.log` files concatenated, prefixed with source |
| `spectator.log` | Spectator | Game creation, table setup, recording |
| `config.json` | `orchestrator.py` | Copy of the game config used |

## Error logging

Errors are written at the source, not pattern-matched after the fact:

- **Python side**: `_log_error(game_dir, username, msg)` in `pilot.py` appends to `{name}_errors.log` and prints to stdout.
- **Java side**: `BridgeCallbackHandler.logError(msg)` appends to the same file. Triggered when `chooseAction()` returns `success: false` or `McpServer` catches an unhandled exception. The file path is passed via `-Dxmage.bridge.errorlog=...`.

Both Python and Java write to the same per-player error file, so errors appear in chronological order regardless of layer.

## What counts as an error

- `choose_action` returning `success: false` (wrong args, index out of range, etc.)
- LLM API errors (timeouts, 402/404, transient failures)
- LLM degradation (10+ consecutive empty responses)
- Stall detection (8+ LLM turns without a successful game action)
- Context trimming (informational but useful for debugging)
- MCP request exceptions (unhandled errors in Java tool handlers)

## Timestamps

All Python log lines use `[HH:MM:SS]` prefix (via `_log()` helper). Java uses the same format via log4j `[HH:mm:ss]` pattern. Error log entries include timestamps from whichever side wrote them.
