# Golden Tests

Integration tests that run real Magic games against a live XMage server with
scripted replay pilots, then compare the captured LLM prompt, game export, and
blunder analysis prompts against checked-in golden files. When code changes
affect any of these outputs, the diff shows up in PRs.

## Why golden tests exist

The LLM's behavior depends entirely on what we send it: the system prompt,
tool definitions, conversation history, and tool results. These are assembled
across two languages (Java game engine + Python orchestration) with several
layers of formatting. A small change in any layer can silently alter what the
model receives.

Golden files make these changes **visible and reviewable**. If a refactor
accidentally drops a field from `get_game_state`, or a prompt tweak changes
the mulligan instructions, the golden file diff shows exactly what changed
in the wire-format payload.

But prompts aren't the only thing that matters. The game export pipeline
(which feeds the leaderboard, game viewer, and blunder analysis) is equally
complex — it merges server logs, LLM events, and decision data from multiple
sources. And blunder analysis prompts need their own golden files because
they're assembled separately from the gameplay prompt, incorporating oracle
card text and decision context. The golden tests cover all three.

## What gets compared

Each golden test produces three kinds of golden files:

### 1. Prompt (`golden/prompts/*.json`)

The complete `messages` array we'd pass to the LLM API — system prompt,
user message, assistant tool calls, tool results. This is the **wire format**,
the exact JSON that would go into the HTTP request body. Tool definitions
are always the same across scenarios and aren't included.

### 2. Export (`golden/exports/*.json`)

The full game export JSON that would be written to `website/public/games/`.
This covers the export pipeline: actions, snapshots, LLM events, LLM trace,
decisions, player summaries, and metadata. Running the export pipeline on the
test's game log directory must produce a result that matches the golden file
(after stripping volatile fields — see below).

### 3. Blunder prompts (`golden/blunder_prompts/<name>/`)

For tests with annotated blunder decisions, the system + user message that
would be sent to the LLM for blunder evaluation. Each annotated decision
gets its own `decision_<N>.json` file, plus an `oracle_cache.json` with
the Scryfall card text used during prompt assembly.

## Architecture: why full integration?

The tests run a real XMage server, real bridge JVMs, and a real spectator —
the same components used in production games. This was a deliberate choice
over lighter alternatives (mocking the server, unit-testing prompt assembly
in isolation, etc.) for several reasons:

**The prompt is assembled across a language boundary.** The Java bridge
receives XMage callbacks, formats game state into JSON, and serves it via
MCP tools. Python reads those tool results and assembles the final prompt.
Mocking either side would test the mock, not the real integration. A change
to how the Java bridge serializes permanents would be invisible to a
Python-only test.

**Game state is complex and stateful.** Setting up a realistic game state
(two bolts on the stack, a Marit Lage token from Dark Depths) requires
actually playing the game. Constructing equivalent state by hand would be
fragile and would drift from what the server actually produces.

**The export pipeline depends on game logs.** The export is built from log
files written during gameplay. Testing it requires actually generating those
logs by playing a game.

The cost is startup time (~30-60s to compile Java and start the server), which
is why golden tests are gated behind `GOLDEN_INTEGRATION=1` and excluded from
`make test`.

## How a golden test works

### Components

A golden test has three inputs:

1. **Deck file** (`puppeteer/tests/decks/*.dck`): A 60-card deck with
   deterministic draw order. The first 7 cards become the opening hand
   (shuffle is disabled via `SKIP_INIT_SHUFFLING`). Cards 8+ are drawn
   in order on subsequent turns. Format: `<count> [<SET>:<NUM>] <Card Name>`.

2. **Replay script**: A Python list of MCP tool calls — the exact sequence
   the AI pilot would make. Each entry is
   `{"name": "tool_name", "arguments": {...}}`.

3. **Golden files**: The checked-in expected outputs (prompt, export, blunder
   prompts). Generated via `make regen-golden`.

### Execution flow

```text
1. Session-scoped fixtures start (once per test run):
   - Compile Java project
   - Start XMage server on a random port
   - Start two bridge JVMs (Player A + Opponent) with keepAlive=true
   - Start spectator/observer JVM with keepAlive=true

2. For each test:
   a. Spectator creates a game table
   b. Both bridges join the table concurrently
   c. Both replay scripts run concurrently:
      - Player A executes the test's script
      - Player B auto-passes (or runs its own script)
   d. Player A's prompt is captured after the script completes
   e. Spectator confirms game end
   f. Compare prompt against golden file
   g. Run export pipeline on game logs, compare against golden file
   h. If script has blunder annotations, compare blunder prompts
   i. Both bridges concede (cleanup for next test)
```

### Why persistent JVMs?

The bridge JVMs are session-scoped (shared across all tests) rather than
started fresh per test. JVM startup is expensive (~5-10s each), and with
3 JVMs (server, bridge A, bridge B) plus the spectator, per-test startup
would dominate the test suite's runtime.

The tradeoff is that a failed test can leave a bridge in a stuck state.
`BridgeManager` handles this with health checks between tests — if the
bridge can't respond to a `tools/list` RPC within 5 seconds, it's killed
and restarted. This prevents cascading failures without paying the full
startup cost for every test.

## Determinism: the priority serialization invariant

The XMage server enforces that **only one player has priority at a time**.
This is a Magic rules requirement, but it's also the foundation of golden
test determinism. Given the same deck (fixed draw order) and the same
replay script (fixed sequence of tool calls), the game state progresses
identically every run.

This invariant means golden tests should never be flaky due to game logic
nondeterminism. If a golden test fails intermittently, the failure has a
real cause — a race condition in the bridge, a timing issue in callback
handling, or a bug in event ordering. See "Debugging flakes" below.

## Handling nondeterminism

Some fields genuinely vary between runs even though the game plays out
identically. The golden test infrastructure normalizes these before
comparison:

### Volatile fields (`_strip_volatile`)

Removed entirely from export comparisons:

- `timestamp` — wall-clock export time
- `ts` on `actions`, `errors`, `llmEvents`, and `llmTrace` — wall-clock timestamps
- `thinkingTimeSecs` — LLM latency (irrelevant in replay mode)
- `latencyMs` on `llmEvents` — provider/runtime timing noise

Sorting applied:

- `llmEvents` and `llmTrace` sorted by `(seq, player)` — both players
  act at the same seq during mulligans, and thread interleaving order
  is nondeterministic

Preserved intentionally:

- top-level export `id` stays in the fixture — golden tests use fixed
  per-scenario directory names, so this field is stable and meaningful
- `errors` entries stay in export goldens after stripping `ts` — a new
  infrastructure error is a real regression and should fail the golden

### Prompt payload normalization (`_normalize_prompt_for_golden`)

Prompt golden comparison keeps prompt payloads literal, including `id` and
`choice` short IDs. The only normalization in this path is parsing embedded
JSON strings and re-serializing them with sorted keys so semantically
identical JSON compares identically.

### Embedded JSON normalization (`_normalize_embedded_json`)

MCP tool results are JSON strings embedded within the messages array. The
key order within these strings can vary between runs
(`{"blocks":"p10","id":"p7"}` vs `{"id":"p7","blocks":"p10"}`). The
normalizer parses and re-serializes these with sorted keys.

It also performs minimal redaction of raw XMage HTML object handles that
survive inside those strings:

- `object_id='123e4567-e89b-12d3-a456-426614174000'` -> `object_id='[redacted]'`
- trailing `</font> [abc]` suffixes -> `</font> [redacted]`

Those UUID/hex handles are run-local presentation noise, not the MCP short
IDs that replay scripts and tool calls use.

### Design principle: never strip to hide a bug

`_strip_volatile` is a whitelist of fields we've verified are genuinely
non-semantic. Adding a field to it to make a flaky test pass is forbidden.
If `game_seq` is nondeterministic, the fix is to make the source
deterministic (e.g. update `lastGameView` from the authoritative callback),
not to strip `game_seq` from comparisons.

## Replay scripts in detail

A replay script is a list of MCP tool calls that drives one player through
a game. The most common pattern alternates `pass_priority` (which blocks
until the player gets priority) and `choose_action` (which selects an action
from the available choices):

```python
script_a = [
    # Mulligan preamble: choose starting player, keep hand
    {"name": "pass_priority", "arguments": {}},
    {"name": "choose_action", "arguments": {"choice": "0"}},
    {"name": "pass_priority", "arguments": {}},
    {"name": "choose_action", "arguments": {"choice": "no"}},

    # T1: Play Mountain
    {"name": "pass_priority", "arguments": {}},
    {"name": "choose_action", "arguments": {"choice": "p14"}},

    # T1: Cast Memnite (0 mana)
    {"name": "pass_priority", "arguments": {}},
    {"name": "choose_action", "arguments": {"choice": "p13"}},

    # End with get_game_state to capture the prompt
    {"name": "get_game_state", "arguments": {}},
]
```

### Chained actions (keeping spells on the stack)

After `pass_priority` returns with playable cards, the bridge auto-passes
once if the player doesn't act. To keep multiple spells on the stack (e.g.
two Lightning Bolts), use **chained `choose_action` calls** without
`pass_priority` between them:

```python
# Cast Bolt #1, target opponent
{"name": "choose_action", "arguments": {"choice": "0"}},
{"name": "choose_action", "arguments": {"choice": "1"}},
# Cast Bolt #2 while #1 is on the stack, target creature
{"name": "choose_action", "arguments": {"choice": "0"}},
{"name": "choose_action", "arguments": {"choice": "p13"}},
```

If you inserted `pass_priority` between the two bolts, the first bolt would
resolve before you could cast the second.

### Card ID prediction

Short IDs are assigned alphabetically by card name, starting at p3 (p1 and
p2 are the players). Within the same card name, IDs are assigned in deck
order. Comments in the script predict the assignments:

```python
# Opponent's 7 Mountains = p3-p9
# TestPlayer's hand (alphabetical): Badlands=p10, LB=p11, LB=p12,
# Memnite=p13, Mountain=p14, Plateau=p15, Taiga=p16
```

### Blunder annotations

Marking a `choose_action` with `"golden_blunder": True` tells the test
infrastructure to also generate and compare a blunder evaluation prompt for
that decision point:

```python
{"name": "choose_action", "arguments": {"choice": "0"}, "golden_blunder": True},
```

## Commands

```bash
make test-golden        # Run all golden tests
make regen-golden       # Regenerate all golden files after intentional changes
make regen-blunder-golden  # Regenerate blunder golden files only
```

`make test-golden` is included in `make check` (which CI runs).
All require `GOLDEN_INTEGRATION=1` (set automatically by the make targets).

## Adding a new test

1. Design a deck in `puppeteer/tests/decks/<name>.dck` — first 7 cards are
   the opening hand, pad to 60 with filler
2. Write the replay script (predict card IDs, plan mana carefully)
3. Create `puppeteer/tests/test_golden_<name>.py` following the existing
   pattern (mark with `@pytest.mark.golden`, call `run_golden_scenario`)
4. Run `make regen-golden` to generate golden files
5. Review the generated files — verify the prompt, export, and any blunder
   prompts look correct
6. Commit everything together

Use the `/golden-test` skill for interactive guidance through this process.

## Debugging flakes

Golden tests should never be flaky. If one fails intermittently, it has a
root cause. Common categories:

**Race conditions:** `game_seq` drift (async `lastGameView` updates),
`bridge_join` timeout (keepAlive loop ordering), empty `get_game_history`
(cache not populated before signal). Fix by reordering operations or using
authoritative callbacks.

**Nondeterministic ordering:** `llmEvents` interleaving at the same seq,
embedded JSON key order, short ID assignment order. Fix in the normalization
layer (these should already be handled by `_strip_volatile` and the
normalizers).

**Logic bugs exposed by timing:** Auto-pass behavior changes, stale
response handling. Fix in the Java bridge code.

Use the `/investigate-golden-flake` skill for structured debugging guidance.

**Never** re-run CI to work around a golden test failure. **Never** modify
`_strip_volatile` to mask a new field. **Never** modify a replay script to
work around nondeterminism. Find and fix the root cause.

## File reference

| Path | Purpose |
| ------ | --------- |
| `puppeteer/tests/golden_helpers.py` | Core infrastructure: `BridgeSession`, `BridgeManager`, `SpectatorProcess`, `run_golden_scenario`, normalization, assertions |
| `puppeteer/tests/conftest.py` | Session fixtures: XMage server, bridge JVMs, spectator |
| `puppeteer/tests/test_golden_*.py` | Individual test files |
| `puppeteer/tests/decks/*.dck` | Deterministic deck files |
| `puppeteer/tests/golden/prompts/*.json` | Golden prompt files |
| `puppeteer/tests/golden/exports/*.json` | Golden export files |
| `puppeteer/tests/golden/blunder_prompts/` | Golden blunder prompt files |
| `.claude/skills/golden-test/SKILL.md` | Skill for adding new tests |
| `.claude/skills/investigate-golden-flake/SKILL.md` | Skill for debugging flakes |
