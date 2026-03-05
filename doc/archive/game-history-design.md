# Structured Game History: Design Notes

## Problem

The pilot needs a structured game log. The current `get_game_log` returns raw
XMage chat text — an unstructured string mixing real game actions with noise
(draw step messages, zone moves, skip attack notifications).

## Rejected approach #1: wrapping XMage chat messages

The first attempt (PR #556) maintained a parallel `List<HistoryEntry>` in
`BridgeCallbackHandler`, populated alongside the existing `gameLog`
StringBuilder in `handleChatMessage`. Turn markers grouped entries; a regex
filtered noise.

This was fundamentally wrong:

1. **Unreliable data source.** XMage delivers chat messages via
   `Session.fireCallback()` with a **50ms lock timeout** for `MESSAGE`-type
   callbacks. When multiple messages arrive in quick succession, the server
   drops any message it can't deliver within 50ms. This makes the chat
   message stream nondeterministic.

2. **No control over content.** We're at the mercy of whatever XMage decides
   to log as chat messages.

3. **Not truly structured.** Despite grouping by turns, it's still just
   XMage text strings in a list.

## Rejected approach #2: diffing consecutive GameView snapshots

GameView snapshots show net state changes between two points in time. This
approach was also rejected because:

1. **Wrong granularity.** Diffs show *what changed* but not *how*. A creature
   moving from battlefield to graveyard could mean destroy, sacrifice, exile
   and return, mill, or any number of game actions. The diff can't distinguish.

2. **Same delivery problem.** GAME_UPDATE callbacks use the same 50ms lock
   timeout. Although less frequent than chat messages, they can still be
   dropped under contention.

3. **No individual action resolution.** Multiple state changes between two
   GameViews are collapsed into a single diff — we lose the sequence of
   individual player actions.

## Correct approach: pull-based bridge event log

Hook into XMage's internal `GameEvent` system, which fires structured
past-tense events (SPELL_CAST, LAND_PLAYED, ATTACKER_DECLARED,
DESTROYED_PERMANENT, etc.) with source/target/player UUIDs after each
action completes. Buffer these on the server, expose a pull API. Zero drops,
server-ordered, complete.

This creates a second log alongside the existing game log (ugly but necessary):
the game log is unreliable XMage text we don't control; the bridge log is
structured data we do.

### Architecture

1. **Server: `GameImpl.fireEvent()` hook.** A single `if (!simulation) {
   recordBridgeEvent(event); }` call added to the existing `fireEvent()`
   method. Selected event types (SPELL_CAST, LAND_PLAYED, ACTIVATED_ABILITY,
   ATTACKER_DECLARED, etc.) are captured into a transient in-memory buffer
   (`List<BridgeLogEntry>`). The buffer is transient so AI simulation copies
   don't carry it.

2. **`BridgeLogEntry` record** (`Mage/mage/game/BridgeLogEntry.java`):
   serializable record with index (pull cursor), gameSeq, event type, turn,
   phase, step, active player, acting player, card name, target name, amount,
   and visibility flag.

3. **Pull API through RPC chain:** `Game.getBridgeEventsSince(cursor, playerId)`
   → `GameController` → `GameManager` → `MageServer` → `Session` →
   bridge client. The `playerId` parameter flows through the entire chain
   for server-side visibility filtering.

4. **Bridge client:** `BridgeCallbackHandler.getGameHistory()` pulls events
   from the server, groups by turn/phase, formats human-readable descriptions
   from the structured fields.

5. **MCP tool:** `get_game_history` — supports `since_turn` and `cursor`
   parameters for incremental access.

### Information visibility

Server-side filtering via `playerId`:

- **Public events** (visibleToAll=true): SPELL_CAST, LAND_PLAYED,
  ATTACKER_DECLARED, BLOCKER_DECLARED, DESTROYED_PERMANENT,
  SACRIFICED_PERMANENT, COUNTERED, BEGIN_TURN, GAINED_LIFE, LOST_LIFE
- **Private events** (visibleToAll=false): DREW_CARD stores the card name
  but only the drawing player sees it. For opponents, `cardName` is redacted.

### Changes to existing XMage code

Only two existing methods touched:

1. `GameImpl.fireEvent()` — 3 lines added
2. `Game.java` interface — 1 method signature added

Everything else is additive (new files, new methods, new implementations).

### Output format

```text
Turn 3 (Alice):
  Precombat Main:
    - Alice played Mountain
    - Alice cast Lightning Bolt targeting Bob's Grizzly Bears
    - Grizzly Bears was destroyed
  Declare Attackers:
    - Alice attacked with Goblin Guide
  Declare Blockers:
    - Bob blocked Goblin Guide with Elvish Mystic
```

### Related: callback drop problem

The root cause of chat message drops is in `Session.fireCallback()`: a 50ms
`callBackLock.tryLock()` timeout for MESSAGE-type callbacks. The pull-based
bridge log sidesteps this entirely — events are buffered server-side and
pulled on demand, with no callback delivery involved.
