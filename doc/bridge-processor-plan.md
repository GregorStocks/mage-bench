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

This is also **not** in a "two PRs away" state anymore.

In broad architectural milestones, a lot of the refactor is done.
In review-sized PRs, the remaining work is still more like **2-4 PRs**,
depending on how aggressively we bundle the remaining projection and
shared-state cleanup.

Write-side MCP actions are much closer to the desired model now:

- `join_table` waits for `START_GAME` through a processor-owned lifecycle flow
- `choose_action`, `pass_priority`, `send_chat_message`, default-response
  helpers, and `concede` all go through processor-owned commands or flows
- `BridgeMcpActionApi` is now a submit/await shell; the stateful action logic
  lives in a processor-side command service instead of in `mcp/`
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
- `get_game_state` snapshot identity now happens at publish time on the
  processor, not lazily on the MCP read path
- `get_action_choices` is now a real read surface rather than a hidden
  auto-resolve path
- the published query-state owner now lives in `processor/`, not `mcp/`
- published game-state and action-choice construction now lives under
  `processor/` too, instead of being built by `BridgeCallbackHandler` and
  passed back in through handler-owned lambdas
- published game state is now projected explicitly from authoritative
  processor-side `GameView` update points instead of being rebuilt from
  `gameState.lastGameView()` on every processor message
- published action choices are now projected explicitly from processor-owned
  pending-action changes and authoritative processor-side `GameView` update
  points instead of being rebuilt during snapshot publication
- published action choices now carry their own backing choice state for
  `choose_action` resolution, so MCP reads and processor-side action execution
  consume the same projected action-choice snapshot instead of sharing a
  separate mutable `DecisionState.lastChoices` bag
- `BridgeMcpQueryApi` is now closer to a read shell:
  - published-state reads now use listener/processor sync barriers and then
    read the published immutable snapshot directly, instead of asking the
    processor thread to read the snapshot on MCP's behalf
  - `get_my_decklist` and `get_oracle_text` now go through a processor-side
    query command service instead of touching handler-owned helpers directly

That API should keep its semantics explicit:

- `get_game_state.snapshot_id` is a snapshot identity / unchanged token
- `get_game_log.cursor` and `get_game_history.cursor` are real stream cursors

But the bridge is still transitional overall:

- raw XMage callbacks now enter through a dedicated `bridge-listener-*` thread
  instead of calling straight into `BridgeCallbackHandler` from arbitrary
  remoting threads
- listener ingress now captures the target handler at enqueue time and performs
  callback decompression / normalization on that listener thread
- callback-dispatch state mutation now lives in a processor-side callback
  service instead of an anonymous `BridgeCallbackHandler` adapter:
  - `START_GAME`
  - pending-action storage
  - passive callback state updates
  - game cleanup / `GAME_OVER` handling
  - callback ingress failure handling
- the live `Bridge*State` holders now sit under a single `BridgeProcessorState`
  owner instead of being flat fields on `BridgeCallbackHandler`
- the published MCP snapshot is now assembled from explicit processor-owned
  projections, but those projections still depend on mutable runtime state and
  helper services at their build points
- MCP reads still need a processor sync barrier before reading the published
  snapshot
- the processor still needs an async `Session.getBridgeEvents(...)` shim to
  append structured bridge events into the local published log
- other MCP reads still depend on shared mutable runtime state holders rather
  than processor-private internals

There are still real actor-model violations or near-violations left:

- persistent keepalive / interaction-limit config now stays on the handler side
  and is applied onto the processor via submitted commands, so fresh-handler
  replacement no longer copies live processor state directly
- the helper stack (`BridgeViewLocator`, `BridgeCardFormatter`,
  `BridgeGameStateBuilder`, `BridgeOracleTextService`, `ShortIdRegistry`) now
  hangs off a processor-side `BridgeProcessorServices` owner instead of being
  stored as separate fields on `BridgeCallbackHandler`
- those helpers no longer carry hidden live-state suppliers; processor-owned
  callers now thread explicit `GameView` / player context into them
- but the helper stack still formats and rebuilds projections from mutable
  runtime bags rather than from native append-only processor projections
- published game state and action choices are now explicit processor-side
  projections, but the projection builders still depend on mutable runtime
  bags and helper services rather than a purely append-only projection model
- bridge-event history still depends on the async
  `Session.getBridgeEvents(...)` synchronization shim

And the bridge still relies on shared mutable state containers such as:

- `BridgeDecisionState`
- `BridgeGameState`
- `BridgeInteractionState`
- `BridgeGameLogState`
- `BridgeCursorState`

Those are still being read outside the processor thread.
So the model is still transitional rather than actor-pure.

One more ownership improvement is now in place:

- choice-resolution metadata and backing lists are no longer stored in
  `BridgeDecisionState`
- the processor-published action-choice projection is now the single source of
  truth for both MCP `get_action_choices` reads and `choose_action` index/id
  resolution

One ownership improvement is now in place:

- choose/pass flow context implementations and their decision-boundary helper
  logic now live under `processor/`
- `BridgeCallbackHandler` no longer serves as the hidden helper bag for those
  flows

But that still does **not** make the bridge actor-pure.

Another remaining smell is that processor-side query publication still depends
on helper classes that are now owned under `BridgeProcessorServices` but still
operate as mutable-state readers rather than native append-only projections.

The biggest remaining MCP-side smell is that some reads still need
processor/barrier synchronization around mutable state holders instead of
reading from purely append-only processor-owned publication structures.

That is better than before, though:

- MCP reads no longer route snapshot retrieval itself back through
  `processor.submit(...)`
- the remaining issue is the barrier and publication model, not "MCP asks the
  processor thread to read mutable state for it"

## Remaining Work

### 1. Replace mutable-state snapshot rebuilds with native published projections

For read surfaces like:

- game state
- action choices
- pending-action visibility

the MCP side already reads the published snapshot, but the projection builders
still depend on mutable `Bridge*State` holders and helper services.

Preferred end state:

- processor appends normalized records to a local published log
- processor publishes immutable snapshots / derived read models
- MCP reads consume those immutable views

This is where the append-only model becomes important.

The game-log/history path is closer to this already.
The game-state/action-choice path is now materially closer: both published game
state and published action choices are projected from explicit processor-side
update points instead of being rebuilt during snapshot publication.

The remaining work in this area is mostly:

- shrinking or deleting query builder helpers that still exist only to rebuild
  projection records from mutable runtime bags
- continuing to narrow `BridgeProcessorServices` so it becomes a thin
  processor-private services/context layer around projections and current-game
  context, not a bag of mutable-state readers

The remaining read-side cleanup should focus on:

- making the rest of MCP reads consume processor-published immutable state
  instead of reading mutable `Bridge*State` holders
- shrinking `BridgeMcpQueryApi` toward a pure "sync barrier + published read"
  shell, with publication/build logic owned elsewhere
- deleting the remaining read helpers that only exist to rebuild projection
  records from those mutable state holders

### 2. Delete the `Session.getBridgeEvents(...)` synchronization shim

Eventually the processor should append authoritative bridge-log records
directly from processor-owned events instead of polling the session and
reconciling later.

### 3. Delete transitional shared-memory machinery

Once reads and writes no longer cross the thread boundary through shared state,
delete the transitional mechanisms that only exist to prop that model up:

- extra `volatile` visibility state
- `synchronized` runtime access used for correctness
- cross-thread latches used as part of live runtime coordination
- shared cursor reconstruction on the MCP side
- any remaining helper APIs whose purpose is "let another thread peek at
  processor-owned state"

## Expected Remaining PRs

Realistically, expect **2-4 review-sized PRs** from here.

A plausible decomposition is:

1. replace more of the published query builders with native processor
   projections so MCP reads stop depending on mutable runtime bags
2. replace the bridge-event sync shim with authoritative processor log appends
3. delete leftover shared-memory scaffolding and simplify barriers
4. split either of the last two steps again if the review gets too large

Some of those could be combined, but only by making the review and failure
surface much larger.

## Definition Of Done

This refactor is done only when all of the following are true:

- listener code can be honestly described as "enqueue-only"
- MCP code can be honestly described as "enqueue commands or read published immutable data"
- all live runtime mutation happens on the processor thread
- no non-processor thread reads live mutable runtime state directly
- `BridgeCallbackHandler` is no longer the place where cross-thread ownership
  is hidden behind helper methods
- MCP naming reflects the actual model: snapshot ids for snapshot reads, cursors
  for append-only log/history reads
- recurring golden flakes caused by shared-memory races are gone, or any
  remaining flakes reduce to deterministic processor-logic bugs

## Non-Goal

This plan is not about preserving the current shape with better packaging.

If we merely move code into different files while MCP code still reads shared
state directly, then we have not achieved the goal of this refactor.
