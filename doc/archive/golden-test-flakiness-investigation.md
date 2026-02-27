# Golden Test Flakiness: `my_turn` Stale Response Race

## Summary

`test_golden_mana_drain_fact_or_fiction` failed ~25% of the time on CI (5/20 recent runs).
Root cause: server-side yield flags + `skip()` create stale responses that answer the
wrong `waitForResponse()`. Fixed by converting all server-side yields to client-side.

## Root Cause

When `pass_priority(until="my_turn")` was called, the bridge sent
`sendPlayerAction(PASS_PRIORITY_UNTIL_MY_NEXT_TURN)` to the XMage server. This dispatched
asynchronously through `callExecutor` (a CachedThreadPool) and ultimately called:

```java
passedAllTurns = true;
this.skip();
```

`skip()` in `HumanPlayer.java` bypasses `waitResponseOpen()`:

```java
public void skip() {
    // waitResponseOpen(); //skip is direct event, no need to wait it
    // TODO: can be bugged and must be reworked, see wantConcede as example?!
    synchronized (response) {
        response.setInteger(0);
        response.notifyAll();
    }
}
```

Meanwhile, the bridge also auto-passes callbacks via `sendPlayerBoolean(false)`, which
goes through `setResponseBoolean()` → `waitResponseOpen()` (spins up to 30s). Both paths
go through the same `callExecutor` pool with no ordering guarantee.

### The Race

1. Bridge dispatches `sendPlayerAction` (Task A) and auto-passes a callback via
   `sendPlayerBoolean` (Task B) — both on `callExecutor`
2. Task B processes first → answers priority P via `setResponseBoolean`
3. Game thread wakes, advances to P+1. `passedAllTurns` NOT YET TRUE → sends callback
4. Task A processes → `passedAllTurns = true; skip()` → answers P+1
5. Bridge receives P+1 callback → auto-passes → `sendPlayerBoolean(false)` (Task C)
6. `passedAllTurns` auto-passes everything until `becomesActivePlayer()` clears it
7. Mana Drain trigger fires → `waitForResponse()` → `responseOpenedForAnswer = true`
8. Task C's `waitResponseOpen()` sees `responseOpenedForAnswer = true` → answers the
   Mana Drain trigger with false → **trigger auto-passed by stale response**

The stale Task C survives because `waitResponseOpen()` spins for up to 30 seconds.

## Fix

Converted all three server-side yields (`my_turn`, `end_of_turn`, `stack_resolved`) to
client-side yields. Instead of `sendPlayerAction` → `skip()`, the bridge auto-passes each
callback locally via `sendPlayerBoolean(false)`. Each callback gets exactly one response —
no duplicates, no stale responses, no race.

### Why Prior PRs Didn't Fix It

- PR #552: Fixed `failedManaCasts` interference — different bug
- PR #553: Fixed `GAME_UPDATE` clobbering `lastGameView` — different bug
- PR #561: Used server-authoritative `land_drops_used` — different bug

All three fixes were correct for their respective issues but didn't address the
fundamental `skip()` / `waitResponseOpen()` race.

## Files Changed

- `BridgeCallbackHandler.java`: Replaced `YIELD_ACTIONS` with `CLIENT_SIDE_YIELDS`,
  added `yieldUntilMyTurn` and `yieldUntilStackResolved` flags in `passPriority()`
- `test_golden_stack_resolved.py`: New golden test for `stack_resolved` yield
