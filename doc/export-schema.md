# Export Schema Design

Reference doc for the export schema formalization effort. See `issues/export-schema-validation.json`.

## Current State (v2)

The `.json.gz` export format is defined implicitly by whatever `scripts/export_game.py` emits and whatever consumers happen to parse. There's no shared schema definition, no validation on either end, and no documentation of field semantics beyond reading the code.

## Seq Number Semantics

The export contains two independent sequence number namespaces that are easily confused:

### Server seq (`seq` in snapshots, actions, gameOver)

- Source: `game.nextGameSeq()` — a monotonic `AtomicInteger` on `GameImpl`.
- Bumped by two call sites:
  1. `GameImpl.informPlayers()` — each game log message gets a unique seq.
  2. `GameController` player query listener — each decision point gets a unique seq.
- **Multiple events can share one seq value.** When a decision point is logged, `ServerGameEventLogCollector.onPlayerQuery()` checks whether the turn/phase changed since the last query and emits synthetic `turn_change` and `phase_change` events with the *same* seq as the decision event. So a single seq value might correspond to: one `turn_change` + one `phase_change` + one `decision` (with snapshot).
- Game actions (`informPlayers`) each get their own unique seq.
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

## Consumers

Code that reads the export format and would need updating if the schema changes:

| Consumer | Language | What it reads |
|----------|----------|---------------|
| `scripts/export_game.py` | Python | Raw logs → export (producer) |
| `website/src/pages/games/[...slug].astro` | Astro/JS | Full export for game replay |
| `website/public/game-renderer.js` | JS | Snapshots, actions, llmEvents for rendering |
| `website/src/pages/leaderboard.astro` | Astro | Player summaries, placements, costs |
| `website/src/pages/games/index.astro` | Astro | Game list metadata |
| `website/src/pages/model-stats.astro` | Astro | Player stats aggregation |
| `website/src/pages/golden.astro` | Astro | Golden test exports |
| `puppeteer/src/puppeteer/leaderboard.py` | Python | Player data, placements for Elo |
| `scripts/analysis/extract_decisions.py` | Python | Snapshots + llmEvents for blunder analysis |
| `scripts/analysis/blunder_analysis.py` | Python | Full export for annotation |

## Migration Framework Design

Goals:
- Bidirectional transforms (v2↔v3) so roundtrip tests can prove no data loss.
- Incremental migration: land schema definition first, migrate games across multiple PRs.
- At most two versions coexist at any time (briefly, during migration).
- Old versions don't accumulate — once migration is complete, delete the old version's migration code.

Approach (TBD during implementation):
- Define the schema in a single source of truth (JSON Schema or Pydantic models).
- Generate TypeScript types from the schema for website consumers.
- Each version bump is a migration module with `up(v2_data) → v3_data` and `down(v3_data) → v2_data`.
- CI test: for every exported game, `down(up(game)) == game` (roundtrip invariant).
- Validation runs on both ends: exporter asserts output matches schema, consumers assert input matches schema.
