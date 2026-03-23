# Bridge Processor Plan

## Goal

The goal is a real actor-style bridge runtime.

We are **not done** until all of the following are true:

- the XMage listener thread only enqueues immutable events onto the processor queue
- the processor thread is the only thread allowed to mutate live bridge runtime state
- MCP/tool threads only:
  - enqueue commands for the processor, or
  - read immutable data published by the processor
- `sendPlayer*` side effects are issued only by the processor thread
- correctness no longer depends on shared `volatile` fields, `synchronized`
  state, latches, or ad hoc compare-and-swap style shared-memory protocols

Package separation by itself does **not** satisfy this goal. Moving code into
`listener/`, `processor/`, and `mcp/` is useful only if it corresponds to the
actual ownership model above.

## Why This Matters

The current bridge still has recurring golden-test flakes and cleanup races.
The expectation is that a true single-writer processor model will either:

- eliminate those flakes outright, or
- reduce them to deterministic processor-state-machine bugs that are much
  easier to reproduce and fix

The key benefit is not aesthetic code organization. It is making ordering and
ownership explicit enough that race conditions stop being a normal failure mode.

## Desired Architecture

### Threads

1. `listener`
   Receives XMage callbacks, decompresses/normalizes them, and enqueues
   immutable processor events. It does nothing else.

2. `processor`
   Owns all live bridge runtime state, processes both callbacks and MCP
   commands, and is the only place that mutates state or sends XMage responses.

3. `mcp`
   Submits commands to the processor and awaits results. For read-only
   surfaces, it reads immutable processor-published data rather than peeking at
   live state.

### Processor scope

Each `BridgeCallbackHandler` / processor pair should be responsible for one
game lifecycle at a time.

If keepAlive wants to join a new game, the correct model is:

- create a fresh handler + processor
- let that new processor own the new game
- shut down or abandon the old processor

The intended architecture is **not** "one processor interleaving multiple
active games." Any leftover multi-game containers or APIs are transitional
vestiges and should be removed.

### Data model

The intended end state should bias strongly toward:

- append-only processor-owned logs
- processor-assigned local monotonic sequence numbers
- immutable published snapshots / derived read views
- minimal mutable in-memory control state

In other words: prefer "append-only log + derived readers" over large shared
bags of mutable fields.

## Current Status

The actual target state has **not** been reached yet.

Write-side MCP actions are much closer to the desired model now:

- `join_table` waits for `START_GAME` through a processor-owned lifecycle flow
- `choose_action`, `pass_priority`, `send_chat_message`, default-response
  helpers, and `concede` all go through processor-owned commands or flows
- the old `gameFinishedLatch` keepAlive concede wait is gone; post-concede
  completion now comes from processor-observed game cleanup

But the bridge is still transitional.

Some MCP reads now go through a processor-published snapshot instead of reading
live state directly:

- pending-action visibility
- game state
- action choices
- game log
- game history

The game-log/history slice is now closer to the intended model:

- the processor publishes a local append-only game log with processor-assigned
  monotonic cursors
- `get_game_log` and `get_game_history` read only that published log
- MCP log/history reads no longer fetch bridge events or read shared
  synchronized log state directly

The action/game-state slice is also closer to the intended model now:

- the processor publishes immutable `get_game_state` and `get_action_choices`
  snapshots after each processed message
- MCP reads sync to that published snapshot instead of rebuilding live state on
  the MCP thread
- `get_action_choices` is now a real read surface rather than a hidden
  auto-resolve path

But the bridge is still transitional overall:

- the published MCP snapshot is still rebuilt from mutable `Bridge*State`
  holders
- the processor still needs an async `Session.getBridgeEvents(...)` shim to
  append structured bridge events into the local published log
- other MCP reads still depend on shared mutable runtime state holders rather
  than processor-private internals

And the bridge still relies on shared mutable state containers such as:

- `BridgeDecisionState`
- `BridgeGameState`
- `BridgeInteractionState`
- `BridgeGameLogState`
- `BridgeCursorState`

Those are still being read outside the processor thread.
So the model is still transitional rather than actor-pure.

## Remaining Work

### 1. Make live runtime state processor-private

The `Bridge*State` classes should stop being cross-thread APIs.

They should become processor-private internals, with non-processor code no
longer reading or mutating them directly.

That includes:

- decision state
- game/lifecycle state
- interaction/mana-plan state
- chat/log state
- cursor/signature state

### 2. Make MCP reads use processor-published data

For read surfaces like:

- game state
- action choices
- game log
- game history
- pending-action visibility

the MCP side should stop reading shared live state directly.

Preferred end state:

- processor appends normalized records to a local published log
- processor publishes immutable snapshots / derived read models
- MCP reads consume those immutable views

This is where the append-only model becomes important.

The game-log/history path now does this.
The game-state/action-choices path now does this too, but it is still rebuilt
from mutable shared state holders.

The remaining read-side cleanup should focus on:

- making the rest of MCP reads consume processor-published immutable state
  instead of reading mutable `Bridge*State` holders
- shrinking or deleting read helpers that only exist to rebuild published
  snapshots from those mutable state holders
- eventually deleting the remaining async `Session.getBridgeEvents(...)` sync
  shim once authoritative log records can be appended directly by the processor

### 3. Delete transitional shared-memory machinery

Once reads and writes no longer cross the thread boundary through shared state,
delete the transitional mechanisms that only exist to prop that model up:

- extra `volatile` visibility state
- `synchronized` runtime access used for correctness
- cross-thread latches used as part of live runtime coordination
- shared cursor reconstruction on the MCP side
- any remaining helper APIs whose purpose is "let another thread peek at
  processor-owned state"

## Definition Of Done

This refactor is done only when all of the following are true:

- listener code can be honestly described as "enqueue-only"
- MCP code can be honestly described as "enqueue commands or read published immutable data"
- all live runtime mutation happens on the processor thread
- no non-processor thread reads live mutable runtime state directly
- `BridgeCallbackHandler` is no longer the place where cross-thread ownership
  is hidden behind helper methods
- recurring golden flakes caused by shared-memory races are gone, or any
  remaining flakes reduce to deterministic processor-logic bugs

## Non-Goal

This plan is not about preserving the current shape with better packaging.

If we merely move code into different files while MCP code still reads shared
state directly, then we have not achieved the goal of this refactor.
