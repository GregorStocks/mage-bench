# Unify Pilot and Blunder Annotator Game Data

## Context

The pilot and blunder annotator see different representations of the same game state:

- **Pilot**: raw JSON from MCP tools (`pass_priority`, `get_action_choices`) — includes board state with `rules` on all cards, `playable` flags, `untapped_lands`, `land_drops_used`
- **Annotator**: reconstructs state post-hoc from server snapshots (rules only on tokens/modified cards, both hands visible) + choices parsed from persisted tool result strings + oracle text from Scryfall API

There are 4+ overlapping representations of "what happened at this decision": server snapshots, inline MCP board payloads, game log text, Scryfall card refs, and summarized tool results. The annotator stitches them together in `extract_decisions.py` and `blunder_analysis.py` with custom formatting. No code is shared with the pilot.

**Goal**: A canonical "decision" format stored in the export, built once, consumed by both the annotator (at analysis time) and the pilot (at game time, via the shared renderer). One place to add fields, fair evaluation, shared rendering.

## Design

### 1. `decisions` array in the export

Add a top-level `decisions` array to the game export. Each entry references a snapshot (by index) and overlays pilot-specific data that isn't in the snapshot:

```json
{
  "index": 0,
  "snapshotIndex": 5,
  "player": "Alice",
  "turn": 3,
  "phase": "PRECOMBAT_MAIN",
  "step": "PRECOMBAT_MAIN",
  "actionType": "GAME_SELECT",
  "responseType": "select",
  "message": "Play spells and abilities",

  "choices": [
    {"index": 0, "name": "Lightning Bolt", "id": "p3", "action": "cast", "mana_cost": "{R}"},
    {"index": 1, "name": "Mountain", "id": "p5", "action": "land"}
  ],
  "choiceCount": 2,
  "isForced": false,

  "pilotContext": {
    "untappedLands": 3,
    "landDropsUsed": 0,
    "playableCards": ["p3"],
    "combatPhase": null,
    "alreadyAttacking": [],
    "incomingAttackers": []
  },

  "chosen": 0,
  "chosenArgs": {"id": "p3"},
  "actionResult": {"success": true, "action_taken": "selected_0"},

  "llmEventIndices": [45, 46, 47],

  "subsequentActions": ["Alice casts Lightning Bolt"],
  "castRolledBack": false
}
```

**Key design decisions:**

- **Board from snapshot reference**: `snapshotIndex` points into `snapshots[]`. No board duplication. The renderer applies hand redaction (opponent hands → `hand_size` only) at render time.
- **Pilot overlay**: `pilotContext` has data the server snapshot doesn't capture: untapped lands, land drops used, which cards are playable, combat phase info. Extracted from the persisted MCP tool result JSON in `llmEvents`.
- **LLM event references, not copies**: `llmEventIndices` is a list of indices into the export's `llmEvents` array, covering all LLM events for this decision (the decision source tool call, any LLM response with reasoning, and the choose_action call). Reasoning, thinking, and full tool results live in `llmEvents` — not duplicated on the decision.
- **Action outcome**: `chosen`, `chosenArgs`, `actionResult` are small and convenient to have directly on the decision. Derived from the `choose_action` event.
- **Subsequent actions**: Short list of game log messages after the action, for annotator context. Filtered to the deciding player only.

### 2. Oracle text strategy

**No `oracleTexts` in the export.** Oracle text comes from the appropriate source at consumption time:

- **Pilot at game time**: The bridge's `pass_priority` board payload already includes `rules` on ALL cards (hand, battlefield, graveyard, exile) via `CardView.getRules()` (BridgeCallbackHandler.java:3612-3616, 3549, 3647-3650, 3668-3671). The renderer extracts these and builds a Card Reference section. No `get_oracle_text` call needed.
- **Annotator at analysis time**: Scryfall cache (`~/.mage-bench/scryfall-cache.json`) provides oracle text. Already cached from previous analysis runs. Network calls happen only on cache miss.
- **Tokens/emblems**: Filtered out of Scryfall lookups (blunder_analysis.py:247). Their rules come from the snapshot's `rules` field on the card object.
- **Split cards, MDFCs, transforms, adventures**: All work via Scryfall's `card_faces` array. Already handled in `_extract_oracle_fields()`.
- **Stack abilities**: Source card name used for Scryfall lookup. Ability text comes from the stack item's `ability_text` field.

The renderer takes an `oracle_texts` parameter. Callers pass whatever's available:

- Pilot: extracted from the bridge board payload (rules on each card object)
- Annotator: fetched from Scryfall cache

### 3. Shared renderer: `decision_renderer.py`

New module at `puppeteer/src/puppeteer/decision_renderer.py`:

```python
def render_decision(
    decision: dict,
    snapshot: dict,
    oracle_texts: dict[str, dict] | None = None,
    *,
    deciding_player: str | None = None,
    include_card_reference: bool = False,
    include_chosen: bool = False,
    prior_context: str = "",
    current_turn_actions: str = "",
) -> str:
```

**Inputs:**

- `decision`: Canonical decision dict (from export or built live from MCP tool result)
- `snapshot`: The referenced snapshot (from export `snapshots[]` or from MCP tool result's `board`)
- `oracle_texts`: Card name → oracle fields dict. Optional. Source varies by caller.
- `deciding_player`: Who's deciding (for hand redaction). When set, opponent hands show only `hand_size`.
- `include_card_reference`: Prepend a Card Reference section listing unique non-basic cards with oracle/rules text
- `include_chosen`: Append chosen action, reasoning (from llmEvents), and subsequent actions — for annotator

**Output format** (structured text):

```text
Turn 3 PRECOMBAT_MAIN - You (Alice, 20 life)
  Board:
    Alice: 20hp hand=[Lightning Bolt {R}, Mountain] bf=[Mountain, Goblin Guide 2/2 (tapped)]
    Bob: 18hp lib=52 bf=[Island]
  Stack: (empty)
  Untapped lands: 2, Land drops: 0/1
  Play spells and abilities (select):
    0: Lightning Bolt [p1, cast, {R}]
    1: Mountain [p2, land]
    Pass: answer=false
```

With `include_card_reference=True`:

```text
Card Reference:
- Lightning Bolt {R} -- Instant: Lightning Bolt deals 3 damage to any target.
- Goblin Guide {R} -- Creature — Goblin Scout 2/2: Haste / Whenever Goblin Guide attacks, ...
```

With `include_chosen=True` (annotator):

```text
  Chosen: Lightning Bolt [p1]
  Reasoning: I should bolt their face.
  After: Alice casts Lightning Bolt
```

### 4. Pilot integration

In `pilot.py`, render `pass_priority` and `get_action_choices` results before presenting to the LLM:

```python
# After execute_tool (line ~681):
result_text = await execute_tool(session, fn.name, args)

# ... existing metadata extraction (game_seq, board_cursor, game_over) — UNCHANGED
# ... existing JSONL logging (logs raw result_text) — UNCHANGED

# NEW: render for LLM display
display_text = result_text
if fn.name in ("pass_priority", "get_action_choices"):
    display_text = _render_for_pilot(result_text, board_tracker)

# Present rendered text to LLM (line ~813):
history.append({"role": "tool", "tool_call_id": tool_call.id, "content": display_text})
```

**`_render_for_pilot(result_text, board_tracker)`**:

1. Parses JSON
2. Splits into snapshot-like dict (`board`/`players`/`stack`/`combat`) and decision-like dict (`choices`, `message`, `pilotContext` fields)
3. Extracts oracle texts from the board payload's `rules` fields on each card
4. Calls `render_decision(decision, snapshot, oracle_texts, include_card_reference=True, deciding_player=...)`
5. Returns rendered text

The raw JSON is still logged to JSONL (unchanged). Only the LLM-facing presentation changes.

**Card reference**: Since the bridge board payload includes `rules` on all cards, the rendered text includes a Card Reference section. This ensures pilots always have oracle text in context without needing to call `get_oracle_text`.

**Board cursor / board_unchanged**: When `pass_priority` returns `board_unchanged: true` without a `board` field, the renderer uses the last-known board. The pilot tracks this (similar to existing `BoardCursorTracker`).

**System prompt update** (`prompts.json`): Updated to describe text format instead of JSON. Simpler instructions since the rendered text is self-documenting.

This changes pilot behavior → **harness epoch bump** required.

### 5. Annotator integration

**`blunder_analysis.py`:**

- Read `decisions` from export directly when present (no more `extract_decisions()` call for new exports)
- Fetch oracle texts from Scryfall cache (same as today, no change)
- Replace `_format_decisions()` / `build_decision_prompt()` with call to `render_decision()` from the shared renderer
- Keep `_format_prior_context()` and `_format_current_turn_actions()` as annotator-specific context builders — pass their output as string params to `render_decision()`
- For `include_chosen=True`, look up reasoning from `llmEvents[decision["llmEventIndices"]]`

**`extract_decisions.py`:**

- `extract_decisions()` reads pre-built `decisions` from export when present
- Legacy path (reconstruct from llmEvents + snapshots) preserved for old exports without `decisions`

### 6. Scryfall considerations

Scryfall handles all standard card types:

- **Split cards**: `card_faces` array, normalized to `" // "` naming
- **MDFCs, transforms, adventures**: `card_faces` array, recursive `_extract_oracle_fields()`
- **Tokens**: Filtered out before lookup (`"Token" not in name`). Rules from snapshot.
- **Emblems**: Not real cards, fail Scryfall lookup gracefully
- **Stack abilities**: Source card name used for lookup; ability text is separate field

No changes needed to Scryfall handling.

## Files to Modify

### New files

| File | Purpose |
| ------ | --------- |
| `puppeteer/src/puppeteer/decision_renderer.py` | Shared `render_decision()` function |
| `puppeteer/tests/test_decision_renderer.py` | Tests for renderer |

### Export pipeline

| File | Change |
| ------ | -------- |
| `schemas/game-export-v2.schema.json` | Add `decisions` array + `Decision` $def (optional field for compat) |
| `scripts/export_game.py` | Add `_build_decisions()`. Call from `build_export()` |
| `doc/export-schema.md` | Update consumers table, describe `decisions` |

### Annotator

| File | Change |
| ------ | -------- |
| `scripts/analysis/extract_decisions.py` | Read pre-built `decisions` when present; keep legacy path |
| `scripts/analysis/blunder_analysis.py` | Use `render_decision()`, read reasoning from llmEvents via indices |

### Pilot

| File | Change |
| ------ | -------- |
| `puppeteer/src/puppeteer/pilot.py` | Render pass_priority/get_action_choices results; track last board for board_unchanged |
| `puppeteer/prompts.json` | Update system prompt for text format |

### Infrastructure

| File | Change |
| ------ | -------- |
| `puppeteer/src/puppeteer/harness_epoch.py` | Bump epoch |

### Tests

| File | Change |
| ------ | -------- |
| `puppeteer/tests/test_blunder_annotator.py` | Update for new code paths |
| `puppeteer/tests/test_blunder_golden_prompts.py` | Regenerate golden prompt files |
| `puppeteer/tests/test_export_schema.py` | Auto-validates against updated schema |

### No Java changes

- MCP tools (BridgeCallbackHandler, McpServer, tool classes) — untouched
- Observer client — untouched
- ServerGameEventLogCollector — untouched

## Implementation Order

1. Write this plan to `doc/unified-decisions-plan.md` (persistent reference)
2. Schema update: add `decisions` to v2 schema (optional field)
3. Decision builder: `_build_decisions()` in `export_game.py` (refactored from `extract_decisions.py`)
4. Shared renderer: create `decision_renderer.py` with `render_decision()`
5. Annotator: update `extract_decisions.py` and `blunder_analysis.py` to use shared renderer
6. Pilot: render tool results in `pilot.py`, update `prompts.json`
7. Tests: new renderer tests + update existing tests
8. Harness epoch bump
9. Re-export all games (batch)
10. Regenerate golden prompt files (`UPDATE_BLUNDER_GOLDEN=1 make test`)
11. `make check`

## Verification

1. `make check` passes (lint, typecheck, tests)
2. `make run` → export includes `decisions` array
3. Blunder analysis on a sample game produces sensible annotations using shared renderer
4. Golden prompt tests updated and passing
5. Schema validation passes on all re-exported games
6. Pilot plays a test game with rendered text format (verify via game logs)

## Key Existing Code to Reuse

- `scripts/analysis/extract_decisions.py:_extract_decisions_v2()` — core decision extraction logic (lines 364-496)
- `scripts/analysis/blunder_analysis.py:_format_decisions()` — current text rendering (reference for output format)
- `scripts/analysis/blunder_analysis.py:_collect_card_names()` — card name extraction from snapshots/choices (lines 187-247)
- `scripts/analysis/blunder_analysis.py:_format_prior_context()` — prior context builder (stays annotator-specific)
- `scripts/analysis/blunder_analysis.py:_format_current_turn_actions()` — current turn actions (stays annotator-specific)
- `scripts/scryfall.py:collection()` — Scryfall batch lookup (lines 59-137)
- `puppeteer/src/puppeteer/pilot.py:_summarize_tool_result()` — existing summarizer (will be partially replaced by renderer)
- `puppeteer/src/puppeteer/pilot.py:BoardCursorTracker` — board cursor tracking (reuse pattern for last-known board)
