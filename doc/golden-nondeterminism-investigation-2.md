# Golden Nondeterminism Investigation - Round 2

Last updated: 2026-02-23
Branch: `lark-howl-globe`

## Objective

Identify root causes of remaining nondeterminism in `make test-golden`. The prior
investigation (doc/archive/golden-nondeterminism-investigation.md) addressed several
sources but flakiness persists end-to-end.

## ROOT CAUSE: lastGameView Backward Overwrite by Stale GAME_UPDATE Callbacks

### Theory

`lastGameView` is a volatile field read by `getGameState()` and `passPriority()`'s
playable-cards check. It is written by both **decision callbacks** (GAME_SELECT, etc.
via `storePendingAction`) and **informational callbacks** (GAME_UPDATE,
GAME_UPDATE_AND_INFORM via `logGameState`).

The XMage server sends GAME_UPDATE to all sessions (players AND watchers —
`GameSessionPlayer` extends `GameSessionWatcher`) during game processing. When the
server resolves a multi-step action (e.g. Fact or Fiction), it calls `informPlayers()`
multiple times, queuing GAME_UPDATE callbacks. Then it reaches a decision point and
sends a GAME_SELECT callback. The game then STOPS and waits for the player's response.

The GAME_UPDATE callbacks from the resolution were queued BEFORE the GAME_SELECT but
can be DELIVERED AFTER it due to the callback delivery mechanism:
- The `ClientCallbackType.UPDATE` has `canComeInAnyOrder=true` — the framework
  explicitly does not guarantee ordering for these
- The callback lock has a 50ms timeout for UPDATE types — they can be dropped or delayed
- Different callbacks execute on different thread pool threads

So a GAME_UPDATE from the resolution (carrying an older `gameSeq`) can overwrite
`lastGameView` AFTER `storePendingAction` already set it to the decision callback's
newer GameView. This is a backward overwrite — `lastGameView` goes from seq N to
seq N-1.

The decision callback's GameView always has the highest `gameSeq` at the point the
player has priority, because it's the last thing the server creates before pausing.
No new callbacks with higher seq should arrive until the player responds.

### Smoking Gun

Captured in `mana_drain_fact_or_fiction` test, reproduced 3/3 failing runs:

```
lastGameView game_seq 68 -> 76 (source=GAME_UPDATE, thread=ThreadPool(1)-4)
lastGameView game_seq 76 -> 77 (source=storePendingAction:GAME_SELECT, thread=ThreadPool(1)-3)
lastGameView game_seq 77 -> 76 (source=GAME_UPDATE, thread=ThreadPool(1)-1)   ← BACKWARD OVERWRITE
get_game_state returns game_seq=76                                              ← READS STALE VALUE
```

The GAME_UPDATE on Thread-1 carries a GameView from the Fact or Fiction resolution
(seq=76), queued before the GAME_SELECT (seq=77) but delivered after it.

### Fix

One-line monotonic guard in `updateLastGameView()`: reject any GameView whose
`gameSeq` is less than the current `lastGameView`'s `gameSeq`. This prevents backward
overwrites while allowing forward updates.

```java
// In updateLastGameView(), before `lastGameView = gv;`:
if (old != null && gv.getGameSeq() < old.getGameSeq()) return;
```

This is safe because:
1. Decision callbacks always have the highest seq (server creates them last)
2. Forward-moving GAME_UPDATEs are harmless (they carry newer state)
3. The only harmful case is backward overwrites, which this prevents

### Expected Observations After Fix

**If the theory is correct:**

1. **Mode 1 (game_seq drift) disappears entirely.** The `mana_drain_fact_or_fiction`
   test should pass 100% of local runs. The backward overwrite (seq 77→76) will be
   rejected by the guard, so `getGameState()` will always read seq=77.

2. **Mode 2 (phase divergence) disappears entirely.** The `savannah_lions_trade` CI
   failure should not recur. GAME_UPDATE callbacks from opponent's phases (which have
   lower seq than the current decision callback) will be rejected, so the playable-cards
   check always sees the correct phase's data.

3. **The diagnostic log will show rejected updates.** We should see new log lines like:
   `"lastGameView REJECTED backward update game_seq 77 -> 76 (source=GAME_UPDATE)"`
   confirming that the guard is firing and preventing the backward overwrites that were
   previously observed.

4. **All other golden tests continue passing.** The guard only prevents backward
   overwrites — forward updates (which are the normal case) are unaffected.

5. **The `roundTracker.update(gv)` call is skipped for rejected views.** This is
   correct: we don't want to update round tracking from stale views either.

**If the theory is WRONG:**

- If golden tests still fail with different game_seq values, there's a source of
  nondeterminism beyond the backward overwrite (e.g. the server generating different
  seq values between runs, or a forward-moving GAME_UPDATE overwriting with wrong
  playable state from a future phase)
- If the guard never fires (no rejected updates in logs), the backward overwrite isn't
  actually happening and the nondeterminism is from something else

## Evidence Summary

### Mode 1: game_seq drift (confirmed, ~60% local failure rate)

- mana_drain_fact_or_fiction: `get_game_state` returns game_seq=76 vs golden 77
- Board state identical — same turn, phase, cards. Only game_seq differs.
- Diagnostic logs prove GAME_UPDATE(76) overwrites GAME_SELECT(77)

### Mode 2: Phase divergence (confirmed on CI run 22290586511)

- savannah_lions_trade: `pass_priority` stops at Declare Attackers vs Precombat Main
- Both runs had actions_passed=6 but different stopping phase
- Playable-cards check at T2 Precombat Main saw wrong view → auto-passed
- 0/10 local reproductions (race window is narrower, CI scheduling differs)

### Server Architecture (why GAME_UPDATE arrives late)

- `GameSessionPlayer` extends `GameSessionWatcher` → players receive GAME_UPDATE
- `ClientCallbackType.UPDATE` has `canComeInAnyOrder=true`
- Callback lock times out at 50ms for UPDATE types → can be delayed/dropped
- During multi-step resolution, multiple GAME_UPDATEs are queued before GAME_SELECT
- Thread pool delivers them concurrently with no FIFO guarantee

## Diagnostic Logging Added (on this branch)

- `updateLastGameView(gv, source)` logs game_seq transitions with source, step, thread
- `passPriority()` always logs playable-cards check details
- `getGameState()` logs the game_seq it returns
- `passPriority()` snapshots `lastGameView` once for the playable check

This logging should be kept temporarily after the fix to verify the guard fires as
expected, then can be removed or reduced to debug level.
