# Export Schema

Game exports live in `website/public/games/` as either `.json` or `.json.gz` files — the format is identical, we just gzip when the file is large enough to annoy GitHub. Both extensions should be treated the same by all consumers.

The format is formally defined by `schemas/game-export-v2.schema.json` (JSON Schema, Draft 7). This is the single source of truth.

TypeScript types are generated from the schema: `website/src/types/game-export.d.ts`. Regenerate with `make schema-types`.

## Validation

- **Exporter**: `scripts/export_game.py` runs lightweight assert checks after `build_export()`.
- **All exports**: `puppeteer/tests/test_export_schema.py` validates every game in `website/public/games/` against the full JSON Schema. Runs as part of `make check`.

## Seq Number Semantics

The export contains two independent sequence number namespaces that are easily confused:

### Server seq (`seq` in snapshots, actions, gameOver)

- Source: `game.nextGameSeq()` — a monotonic `AtomicInteger` on `GameImpl`.
- Bumped by three call sites:
  1. `GameImpl.informPlayers()` — each game log message gets a unique seq.
  2. `GameController` player query listener — each decision point gets a unique seq.
  3. `ServerGameEventLogCollector.onGameLog()` — each `turn_change` and `phase_change` event gets a unique seq.
- **Every event has a unique seq value.** No two events share a seq.
- `game_end` gets its own seq via `nextGameSeq()`.

### LLM seq (`seq` in llmEvents, llmTrace)

- Source: `GameLogWriter._seq` — a per-player, per-writer monotonic counter.
- Completely independent of server seq. Starts at 1 for each player.
- Cross-referenced to server seq via the `gameSeq` field on LLM events (when available).
- `gameSeq` is populated from the bridge's `game_seq` field in JSONL, or extracted from tool call result JSON.

### What a snapshot "means"

A snapshot is a complete game state captured at a decision point — specifically, when `onPlayerQuery()` fires (before the player responds). The snapshot's `seq` is the server seq of that decision. The snapshot represents "the world as it was when this decision was presented."

Snapshots are deduplicated in raw logs by hash (repeated identical states store `state_hash` instead of `state`), but exports expand all snapshots fully.

### Ordering guarantees

Within the server event log, events are ordered by seq (with ties broken by emission order: turn_change, phase_change, decision). Across the merged game log (`game.jsonl`), events from different sources (server, LLM) are sorted by server seq first, then timestamp, then read order.

## Decisions

The `decisions` array contains canonical decision records built at export time. Each decision references a snapshot (by index) and overlays pilot-specific context (choices, playable cards, combat info, etc.) that isn't in the server snapshot.

Decisions are the shared data format consumed by both the pilot (at game time, via the shared renderer) and the blunder annotator (at analysis time). See `doc/unified-decisions-plan.md` for the full design.

Key fields:
- `snapshotIndex` — points into `snapshots[]` for the board state
- `choices` — available choices from the MCP tool result
- `pilotContext` — overlay data (untapped lands, land drops, playable cards, combat info)
- `llmEventIndices` — indices into `llmEvents[]` covering this decision's LLM interactions
- `chosen`, `chosenArgs`, `actionResult` — what the player did
- `subsequentActions` — game log messages after the action resolved

Oracle text is NOT stored in the export. The shared renderer (`decision_renderer.py`) accepts oracle texts as a parameter — the pilot extracts them from the bridge board payload's `rules` fields, and the annotator fetches them from the Scryfall cache.

## Consumers

Code that reads the export format and would need updating if the schema changes:

| Consumer | Language | What it reads |
|----------|----------|---------------|
| `scripts/export_game.py` | Python | Raw logs -> export (producer) |
| `website/src/pages/games/[...slug].astro` | Astro/JS | Full export for game replay |
| `website/public/game-renderer.js` | JS | Snapshots, actions, llmEvents for rendering |
| `website/src/pages/leaderboard.astro` | Astro | Player summaries, placements, costs |
| `website/src/pages/games/index.astro` | Astro | Game list metadata |
| `website/src/pages/model-stats.astro` | Astro | Player stats aggregation |
| `website/src/pages/golden.astro` | Astro | Golden test exports |
| `puppeteer/src/puppeteer/leaderboard.py` | Python | Player data, placements for Elo |
| `scripts/analysis/extract_decisions.py` | Python | Snapshots + llmEvents for blunder analysis |
| `scripts/analysis/blunder_analysis.py` | Python | Full export for annotation |
| `puppeteer/src/puppeteer/decision_renderer.py` | Python | Decisions + snapshots for shared rendering |

## Evolving the Schema

**Never re-export games from raw logs** to pick up schema changes. Raw logs are the pre-export format and may not be available for older games. Instead:

1. **Additive optional fields** (derivable from existing export data): Add the field to the schema, update `export_game.py` for new exports, and write a backfill script in `scripts/` to patch existing exports in-place (see `backfill_decisions.py` for the pattern).

2. **Breaking changes**: Bump the export version and write a bidirectional migration in `schemas/migrations/` (see below). Land the schema first, then migrate games incrementally.

3. **Manual backfill**: As a last resort when data can't be derived from the export and raw logs must be consulted. Avoid this — it's fragile and doesn't scale.

## Migration Framework

Migration modules live in `schemas/migrations/`. See `schemas/migrations/README.md` for the pattern.

Goals:
- Bidirectional transforms (v2<->v3) so roundtrip tests can prove no data loss.
- Incremental migration: land schema definition first, migrate games across multiple PRs.
- At most two versions coexist at any time (briefly, during migration).
- Old versions don't accumulate -- once migration is complete, delete the old version's migration code.
