# Architecture

mage-bench evaluates LLMs by having them play Magic: The Gathering through a full-rules game engine. The system has five main components: a game server, bridge clients that expose MCP tools, a Python orchestrator that runs LLM agents, a spectator that records games, and a post-game analysis pipeline.

## Game Engine

Games run on a nearly-stock [XMage](https://github.com/magefree/mage) server. The only changes compared to upstream are around logging and error handling — not the actual game engine. The server tracks a monotonic `game_seq` counter (`GameImpl.nextGameSeq()`) that tags every game event with a unique sequence number, used downstream for deterministic event ordering. We aspire to contribute all game-engine bugfixes back to upstream.

## Bridge

Each player is backed by a headless Java client (`BridgeCallbackHandler.java`) that connects to the XMage server using the standard client API. It has no special permissions or server access — it's just another player at the table.

Instead of rendering a UI, the bridge exposes [MCP](https://modelcontextprotocol.io) (Model Context Protocol) tools via HTTP JSON-RPC 2.0 on `127.0.0.1:{port}/mcp`. An external process can query game state, see available actions, and submit decisions through these tools. The available tools are: `pass_priority`, `get_action_choices`, `choose_action`, `get_game_state`, `get_game_log`, `get_oracle_text`, `send_chat_message`, and `concede`.

The bridge now runs in a single MCP-oriented mode:

- **sleepwalker**: Exposes pending actions via MCP and can auto-pass simple flows without an LLM. Used for infra and deterministic testing.
- **pilot**: A Python LLM loop layered on top of the same MCP bridge. This is the primary evaluation path.
- **replay**: A scripted Python controller layered on top of the same MCP bridge for golden tests.
- **cpu**: XMage's built-in AI and not a bridge mode.

The bridge also handles several things automatically so the LLM doesn't have to micromanage:

- **Auto-tapping lands**: When the game needs mana, the bridge resolves mana abilities that only cost `{T}` (no additional mana cost). If a pilot provides a `manaPlan`, it follows that plan and falls back to auto-tap if the plan is exhausted.
- **Auto-passing**: The bridge auto-passes in several contexts: loop detection (>25 interactions per turn), client-side yields to a target phase/step, and when no playable cards are available. Client-side yields use `sendPlayerBoolean(false)` rather than server-side `skip()` to avoid race conditions with stale callback responses.
- **Action filtering**: Unplayable actions are filtered from choices presented to the pilot.

## Puppeteer

The puppeteer is split into two Python components: the **orchestrator** that manages processes, and the **pilot** that runs the LLM agent loop.

### Orchestrator

`orchestrator.py` manages the full game lifecycle:

1. Compile Java project (server, client modules, bridge, observer)
2. Start XMage server on a chosen port, wait for it to accept connections
3. Start the spectator, which creates the game table (polls spectator log for a ready marker, 300s timeout)
4. Spawn bridge clients — one per player, in sequence
5. Spawn pilot processes — one per LLM player (polls for "all players joined" marker, 600s timeout)
6. Monitor all processes with a 2s poll interval. If any pilot exits non-zero, abort the entire game
7. After the game ends, merge logs: `server_game_events.jsonl` + all `*_llm.jsonl` → unified `game.jsonl` (sorted by `game_seq` then timestamp)
8. Print summary: winner, life totals, turn count, API costs

The orchestrator supports running multiple games in parallel with staggered startup, unique spectator usernames, and isolated game directories.

### Pilot

`pilot.py` is a per-player Python subprocess that runs an async LLM agent loop:

1. Call `pass_priority` — blocks until the game needs a decision
2. Call `get_action_choices` — get available choices with board context
3. Render the game state for the LLM via `render_decision()` (shared with blunder analysis)
4. Send to an LLM via `AsyncOpenAI` (works with OpenRouter, Anthropic, any OpenAI-compatible API)
5. Route the LLM's tool calls back through MCP to the bridge
6. Repeat until game ends

**Context window management**: The pilot maintains a bounded message history. The most recent 40 messages are kept at full fidelity. Up to 20 older messages are included with tool results summarized to ≤200 characters. The context is re-rendered every 5 iterations when history is long. Two cache-control breakpoints enable prompt caching: one after the system + summarized prefix (stable across iterations), and one at the state bridge message.

**Error handling**: LLM requests have a 120s timeout. On timeout, the pilot auto-passes and continues. After 3 consecutive timeouts, it aborts. Permanent API failures (401/402/403/404) cause the pilot to exit with code 3, which triggers the orchestrator to abort the game. A stall detector tracks turns without game progress — after 20 stalled turns, the pilot enters an auto-pass loop and resets its context.

**Shared decision renderer**: `decision_renderer.py` provides `render_decision()`, which formats board state (life totals, hands, battlefields, graveyards, exile, stack, combat) into structured text. The same renderer is used by the pilot at game time and by the blunder annotator at analysis time, ensuring the LLM sees an identical representation in both contexts.

## Spectator

A separate Java client (`Mage.Client.Observer/`) connects as a spectator and automatically requests permission to see all players' hands. It records two things:

- **Video**: Renders the full game state visually (battlefield, hands, graveyards, stack) and pipes frames to FFmpeg for H.264 encoding. Pixel-identical frames are skipped to produce variable-frame-rate video.
- **Structured event log**: Writes `server_game_events.jsonl` with one JSON object per line. Event types include `state_snapshot` (full board state at decision points, deduplicated by hash), `game_action` (log messages), `player_chat`, and `game_over`. Each event is tagged with the server's `game_seq` for deterministic ordering.

## Game Export

`scripts/export_game.py` transforms raw game logs into website-ready JSON files that power the game viewer, leaderboard, and blunder analysis.

The export pipeline:

1. Reads source data: `server_game_events.jsonl`, all `*_llm.jsonl` files, `*_llm_trace.jsonl` files, `game_meta.json`, and error logs
2. Builds **canonical decisions** by finding decision sources (`pass_priority` or `get_action_choices` with `action_pending=true`), matching each to the nearest snapshot by `game_seq`, resolving what the player actually chose, and collecting up to 5 subsequent game actions to show consequences
3. Extracts per-decision **pilot context**: untapped lands, land drops remaining, combat phase, playable cards
4. Computes per-player stats: thinking time, tool call success/failure counts, API cost
5. Fetches card images from Scryfall
6. Outputs to `website/public/games/{game_id}.json` (gzipped to `.json.gz` if >25 MiB)

Error classification filters LLM-caused errors (invalid tool calls, empty responses, stalls) from infrastructure bugs, so only real bugs surface on the website.

## Blunder Analysis

`scripts/analysis/blunder_analysis.py` reviews every non-forced decision in an exported game using Claude Opus via OpenRouter.

For each decision, the pipeline renders the full board context via `render_decision()` (with prior board state from 2 turns back, action log deltas, and card oracle text) and sends it to Claude for classification. Decisions are rated at four severity levels:

- **Questionable**: Probably suboptimal but debatable (~30% confidence it was wrong)
- **Minor**: Clearly suboptimal, small value lost (sequencing, fetch choice)
- **Moderate**: Real mistake with meaningful consequences (wasted card, missed significant line)
- **Major**: Game-losing or near it (threw away win, missed lethal)

Each annotation includes the severity, a description of what went wrong, what the player did, and what they should have done. Results appear in the game viewer and feed into the blunder index on the leaderboard (see `/scoring` on the website for the formula). The analysis script has its own version counter (currently v31) separate from the harness epoch.

## Harness Epochs

`HARNESS_EPOCH` (currently 37) is a monotonic integer in `puppeteer/src/puppeteer/harness_epoch.py` that tracks breaking changes to the evaluation harness. It gets bumped when MCP tools, pilot logic, or priority semantics change enough to make game results non-comparable across versions.

Ratings are per-season and the leaderboard shows the current season. New games write `season` directly to `game_meta.json` at run time. For older games that predate this, `SEASON_1_START_EPOCH` (= 11) in `harness_epoch.py` is used at export time to assign `season: 0` or `season: 1`.

## Testing

Golden tests exercise the full MCP pipeline end-to-end: set up a real XMage game state, run the bridge, capture what it would send to the LLM, and snapshot the result for regression testing. See the [golden test scenarios](/golden) on the website to inspect exactly what the LLM sees for each test case.

## Logging

Game logs go to `~/.mage-bench/logs/game_YYYYMMDD_HHMMSS/`. See [logging.md](logging.md) for the full file layout. Key files per game:

- `server_game_events.jsonl` — spectator-captured events with `game_seq`
- `{name}_llm.jsonl` — per-player LLM events (responses, tool calls, errors, stalls)
- `{name}_llm_trace.jsonl` — full LLM API request/response pairs
- `{name}_cost.json` — cumulative API cost in USD
- `{name}_errors.log` — per-player errors (real-time writes)
- `game.jsonl` — unified merged log (sorted by `game_seq` then timestamp)
- `game_meta.json` — metadata (players, decks, models, harness epoch, git info)
