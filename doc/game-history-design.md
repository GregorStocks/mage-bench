# Structured Game History: Design Notes

## Problem

The pilot needs a structured game log. The current `get_game_log` returns raw
XMage chat text — an unstructured string mixing real game actions with noise
(draw step messages, zone moves, skip attack notifications).

## Failed approach: wrapping XMage chat messages

The first attempt (PR #556) maintained a parallel `List<HistoryEntry>` in
`BridgeCallbackHandler`, populated alongside the existing `gameLog`
StringBuilder in `handleChatMessage`. Turn markers grouped entries; a regex
filtered noise.

This was fundamentally the wrong approach:

1. **Unreliable data source.** XMage delivers chat messages via
   `Session.fireCallback()` with a **50ms lock timeout** for `MESSAGE`-type
   callbacks (see `Mage.Server/.../Session.java:432-456`). When multiple
   messages arrive in quick succession (e.g., Fact or Fiction resolving 5
   zone-move messages nearly simultaneously), the server drops any message
   it can't deliver within 50ms. This makes the chat message stream
   nondeterministic — golden tests showed the same game producing different
   message counts across runs.

2. **No control over content.** We're at the mercy of whatever XMage decides
   to log as chat messages. Some actions produce multiple redundant messages,
   some produce none. We can filter noise with regex, but we can't add
   information that XMage doesn't emit.

3. **Not truly structured.** Despite grouping by turns, it's still just
   XMage text strings in a list. The "structure" is superficial — we have
   no semantic understanding of what each action represents.

## Correct approach: diff consecutive GameView snapshots

The bridge already receives `GAME_UPDATE` callbacks containing full
`GameView` snapshots. These are the authoritative source of game state —
they're how the bridge knows what cards are on the battlefield, what's in
hand, what life totals are, etc.

By comparing consecutive GameView snapshots, we can construct a complete,
reliable, token-efficient game history with full control over the format.

### What's in a GameView

Each GameView is a complete snapshot:

- **Turn/phase**: `turn`, `phase`, `step`, `activePlayerId`,
  `activePlayerName`, `priorityPlayerName`, `gameSeq`
- **Per player** (via `PlayerView`):
  - `life`, `manaPool`, `counters`, `monarch`, `initiative`
  - `libraryCount`, `handCount`
  - `battlefield` (Map<UUID, PermanentView>) — every permanent with full state
  - `graveyard`, `exile` (CardsView)
  - `landsPlayed`, `landsPerTurn`
- **Stack**: spells and abilities currently on the stack
- **Combat**: attackers, blockers, defenders
- **Sequence**: `gameSeq` — monotonic counter from the server, used to
  detect out-of-order or backward updates

### Diffing strategy

Store the previous GameView. When a new one arrives, compare:

1. **Life total changes**: `prevLife != currLife` → "Player took 3 damage"
   or "Player gained 2 life"
2. **Zone transitions**: cards appearing in/leaving battlefield, graveyard,
   exile, stack. UUIDs let us track specific cards across zones:
   - Card appears on battlefield → "Player played [card]" or spell resolved
   - Card moves from battlefield to graveyard → "Card died" / "Card was
     destroyed"
   - Card appears on stack → "Player cast [spell]"
   - Card leaves stack to battlefield → spell resolved
   - Card leaves stack to graveyard → spell countered or fizzled
3. **Permanent state changes**: tapped/untapped, counters added/removed,
   damage, attachments
4. **Combat changes**: new attackers/blockers declared
5. **Turn/phase transitions**: turn number changes, phase advances

### Advantages over chat-message approach

- **Reliable**: GameView snapshots are the source of truth. No 50ms timeout
  drops. `gameSeq` detects out-of-order delivery.
- **Complete**: every state change is visible in the diff, even if XMage
  doesn't emit a chat message for it.
- **Token-efficient**: we control the format. We can produce concise,
  information-dense summaries instead of XMage's verbose chat text.
- **Truly structured**: each history entry is a semantic event (card played,
  spell cast, creature died) not an opaque text string.

### Implementation sketch

```java
// In BridgeCallbackHandler:

private GameView previousGameView = null;

// Called from GAME_UPDATE handler, after updateLastGameView():
private void recordHistoryDiff(GameView prev, GameView curr) {
    if (prev == null) return; // first update, nothing to diff

    // Detect turn change
    if (!Objects.equals(prev.getActivePlayerName(), curr.getActivePlayerName())
        || prev.getTurn() != curr.getTurn()) {
        // New turn entry
    }

    // Diff each player's state
    for (PlayerView currPlayer : curr.getPlayers()) {
        PlayerView prevPlayer = findPlayer(prev, currPlayer.getName());
        if (prevPlayer == null) continue;

        // Life changes
        if (prevPlayer.getLife() != currPlayer.getLife()) { ... }

        // Battlefield changes (new permanents, removed permanents)
        Set<UUID> prevBF = prevPlayer.getBattlefield().keySet();
        Set<UUID> currBF = currPlayer.getBattlefield().keySet();
        // New permanents: currBF - prevBF
        // Removed permanents: prevBF - currBF

        // Graveyard additions
        // ... etc
    }

    // Stack changes
    // Combat changes
}
```

### Open questions

- **Granularity**: how often do GAME_UPDATE callbacks arrive? If they batch
  multiple state changes, we might miss intermediate states. Need to verify
  whether each action triggers its own GAME_UPDATE or if they're batched.
- **Hidden information**: the bridge only sees public information and its own
  hand. Opponent's hand contents aren't in GameView unless revealed. This is
  fine — the pilot shouldn't know hidden info anyway.
- **Performance**: diffing full GameView objects on every update. Should be
  cheap since it's just comparing maps/sets, but worth measuring.
- **Interaction with get_game_log**: should the existing text-based log
  remain as-is, with the new structured history as a separate tool? Probably
  yes — the text log is useful for debugging and the structured history is
  for the pilot.

### Callback delivery reliability

GAME_UPDATE is `ClientCallbackType.UPDATE` which has `canComeInAnyOrder=true`.
It gets the same 50ms lock timeout as MESSAGE. However, GAME_UPDATE callbacks
are less prone to contention because:

1. They follow a request-response pattern (server waits for player decisions
   between updates)
2. They're less frequent than chat messages during spell resolution
3. `gameSeq` lets us detect if any are missing

If GAME_UPDATE drops become an issue, we could increase the lock timeout for
UPDATE callbacks server-side (a minimal upstream bug fix). But this is likely
not needed.

### Related: server-side message drops

The root cause of chat message drops is in `Session.fireCallback()`
(`Mage.Server/.../Session.java:432-456`): a 50ms `callBackLock.tryLock()`
timeout for MESSAGE-type callbacks. When multiple callbacks arrive in quick
succession, the lock is held by the previous send and subsequent messages
time out. The server logs `"CALLBACK DROPPED (lock timeout 50ms)"` but the
client has no way to know a message was lost.

This is an XMage bug (affects the Swing UI too) but fixing it isn't necessary
for the structured history if we use GameView diffs instead of chat messages.
It may still be worth filing upstream.
