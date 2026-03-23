# Export Schema

Game exports live in `website/public/games/` as either `.json5` or `.json5.gz`
files. The on-disk payload is the same either way; large files are gzipped to
keep the repo manageable.

`src/magebench/game/game-export-v9.schema.json` is the current canonical export contract.
TypeScript types are generated from that schema today. If a future change needs
another persisted format version, add the new schema and any migration/backfill
plan in that PR instead of keeping retired v2-v7 history around indefinitely.

TypeScript types are generated from the latest schema: `website/src/types/game-export.d.ts`. Regenerate with `make regen-schema-types`.

## Validation

- **Exporter**: `scripts/export_game.py` runs lightweight assert checks after `build_export()`.
- **All exports**: `tests/test_export_schema.py` validates every game in `website/public/games/` against the full JSON Schema. Runs as part of `make check`.

## Seq Number Semantics

The export contains two independent sequence number namespaces that are easily confused:

### Server seq (`seq` in snapshots, actions, game_over)

- Source: `game.nextGameSeq()` — a monotonic `AtomicInteger` on `GameImpl`.
- Bumped by three call sites:
  1. `GameImpl.informPlayers()` — each game log message gets a unique seq.
  2. `GameController` player query listener — each decision point gets a unique seq.
  3. `ServerGameEventLogCollector.onGameLog()` — each `turn_change` and `phase_change` event gets a unique seq.
- **Every event has a unique seq value.** No two events share a seq.
- `game_end` gets its own seq via `nextGameSeq()`.

### LLM seq (`seq` in llm_events, llmTrace)

- Source: `GameLogWriter._seq` — a per-player, per-writer monotonic counter.
- Completely independent of server seq. Starts at 1 for each player.
- Cross-referenced to server seq via the `game_seq` field on LLM events (when available).
- `game_seq` is populated from the bridge's `game_seq` field in JSONL, or extracted from tool call result JSON.

### What a snapshot "means"

A snapshot is a complete game state captured at a decision point — specifically, when `onPlayerQuery()` fires (before the player responds). The snapshot's `seq` is the server seq of that decision. The snapshot represents "the world as it was when this decision was presented."

Snapshots are deduplicated in raw logs by hash (repeated identical states store `state_hash` instead of `state`), but exports expand all snapshots fully.

### Ordering guarantees

Within the server event log, events are ordered by seq (with ties broken by emission order: turn_change, phase_change, decision). Across the merged game log (`game.jsonl`), events from different sources (server, LLM) are sorted by server seq first, then timestamp, then read order.

## Decisions

The `decisions` array contains canonical decision records built at export time. Each decision references a snapshot (by index) and overlays pilot-specific context (choices, playable cards, combat info, etc.) that isn't in the server snapshot.

Decisions are the shared data format consumed by both the pilot (at game time, via the shared renderer) and the blunder annotator (at analysis time). See `doc/unified-decisions-plan.md` for the full design.

Key fields:

- `snapshot_index` — points into `snapshots[]` for the board state
- `choices` — available choices from the MCP tool result
- `pilot_context` — overlay data (untapped lands, land drops, playable cards, combat info)
- `llm_event_indices` — indices into `llm_events[]` covering this decision's LLM interactions
- `chosen`, `chosen_args`, `action_result` — what the player did
- `subsequent_actions` — game log messages after the action resolved

Oracle text is NOT stored in the export. The shared renderer (`decision_renderer.py`) accepts oracle texts as a parameter — the pilot extracts them from the bridge board payload's `rules` fields, and the annotator fetches them from the Scryfall cache.

## Consumers

Code that reads the export format and would need updating if the schema changes:

| Consumer | Language | What it reads |
| ---------- | ---------- | --------------- |
| `scripts/export_game.py` | Python | Raw logs -> export (producer) |
| `website/src/pages/games/[...slug].astro` | Astro/JS | Full export for game replay |
| `website/public/game-renderer.js` | JS | Snapshots, actions, llm_events for rendering |
| `website/src/pages/leaderboard.astro` | Astro | Player summaries, placements, costs |
| `website/src/pages/games/index.astro` | Astro | Game list metadata |
| `website/src/pages/model-stats.astro` | Astro | Player stats aggregation |
| `website/src/pages/golden.astro` | Astro | Golden test exports |
| `src/magebench/leaderboard/leaderboard.py` | Python | Player data, placements for Elo |
| `scripts/analysis/extract_decisions.py` | Python | Snapshots + llm_events for blunder analysis |
| `scripts/analysis/blunder_analysis.py` | Python | Full export for annotation |
| `puppeteer/src/puppeteer/decision_renderer.py` | Python | Decisions + snapshots for shared rendering |

## Evolving the Schema

**Never re-export games from raw logs** just to pick up export changes. Raw logs
are the pre-export format and may not be available for older games.

When the export contract changes:

1. **Current-schema updates**: If the on-disk format stays v9, update
   `src/magebench/game/game-export-v9.schema.json`, regenerate the derived TypeScript types, and
   backfill committed exports in place as needed.

2. **New persisted format versions**: If the change genuinely needs a new
   version, add the new schema and migration/backfill machinery in that same
   PR. Do not keep obsolete version stacks around after all committed exports
   have moved forward.

3. **Non-schema repairs**: Use a backfill script only when the exported
   contract is unchanged and you are repairing derived data in place.

4. **Manual backfill**: As a last resort when data cannot be derived from the
   export and raw logs must be consulted.
