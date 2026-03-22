# Bridge Processor Refactor Plan

## Context

`BridgeCallbackHandler.java` still acts as both:

- the XMage callback listener
- the processor-owned state machine for pending decisions
- the MCP-facing query/command surface
- the place where `sendPlayer*` side effects happen

That means multiple threads currently touch the same mutable state:

- callback delivery thread(s) update pending decision state
- MCP handler threads read and mutate that same state via `pass_priority`, `choose_action`, `get_action_choices`, and related helpers
- keepAlive / lifecycle signaling also shares the same object

The bridge has accumulated `volatile` fields, `wait()/notifyAll()`, ad hoc compare-and-swap clears, latches, and thread-sensitive helper semantics to keep this working. Recent fixes like the `pendingActionReady` handshake are valid, but they are also a signal that the current ownership model is wrong: the architecture makes races easy to create and hard to reason about.

This document is the persistent plan for moving the bridge to a single-owner-thread processor model.

Related issues:

- [issues/p3-extract-bridge-runtime-loop.json5](/home/gregor/code/worktrees/bridge-cairn-salt/issues/p3-extract-bridge-runtime-loop.json5)
- [issues/p3-extract-bridge-decision-surface.json5](/home/gregor/code/worktrees/bridge-cairn-salt/issues/p3-extract-bridge-decision-surface.json5)
- [issues/p3-extract-bridge-mana-handler.json5](/home/gregor/code/worktrees/bridge-cairn-salt/issues/p3-extract-bridge-mana-handler.json5)

## Goal

Move the bridge toward an actor-style processor architecture:

- one listener thread receives XMage callbacks and enqueues immutable bridge events
- one processor thread owns all mutable bridge processor state
- MCP/tool threads do not read or mutate processor-owned fields directly
- MCP/tool threads communicate with the processor through commands and await results
- `sendPlayer*` responses are serialized through the processor thread

In short: no shared mutable processor state across threads, only message passing.

## Non-Goals

This plan does not require:

- rewriting the MCP API surface
- changing game semantics
- introducing a separate process
- landing an append-only published read model in the first refactor

The first milestone is about state ownership and side-effect ordering, not about polishing every API around it.

## Desired Architecture

### Threads

1. **Listener thread**
   Receives XMage callbacks and turns them into immutable `BridgeEvent` values.
   It does not mutate processor-owned state directly.

2. **Processor thread**
   Owns `BridgeProcessorState`, processes callback events and MCP commands in a single serialized stream, and is the only place allowed to call `sendPlayer*`.

3. **MCP/tool threads**
   Submit `BridgeCommand`s and await typed results. They do not read bridge processor fields directly.

### Core types

The design should use explicit processor-oriented names, something close to:

- `BridgeProcessorState`
- `BridgeEvent`
- `BridgeCommand`
- `BridgeCommandResult`
- `BridgeProcessor`

`BridgeCommand` handlers should return via `CompletableFuture` (or equivalent), so callers can block synchronously without sharing memory.

### Code organization

This refactor should improve code layout, not just thread ownership.

Constraints:

- do not grow `BridgeCallbackHandler.java` with new processor abstractions
- keep the processor code in its own directory/package, not as nested helper classes inside the handler
- default to one class per file unless there is a strong reason not to
- aim for separate top-level areas for the three major pieces:
  listener/callback ingress, processor state/event/command loop, and MCP/tool-facing command/query surface
- keep `BridgeProcessor` generic infrastructure only; callback-specific dispatch/apply logic should live in separate classes under the processor package

Likely shape:

- `mage.client.bridge.listener`
- `mage.client.bridge.processor`
- `mage.client.bridge.mcp` (or equivalent)

### Ownership rules

Processor-owned state should include, at minimum:

- pending decision state
- `lastGameView` / game-seq-related state
- last-choices snapshots
- mana-plan state
- turn counters / loop detection state
- keepAlive game lifecycle state
- unseen chat / bridge-event cursors

The listener thread should only enqueue events.

The MCP/tool layer should only:

- construct commands
- await results
- translate results into tool responses

## Why This Is Better

This refactor should eliminate an entire class of bugs where:

- a callback is only partially processed when a tool thread observes it
- one thread clears or replaces pending state while another thread is still using it
- `lastGameView`, `pendingAction`, or choice snapshots are read at slightly different times by different threads
- `sendPlayer*` calls race with callback processing or with each other

It also gives a simpler correctness rule:

> If it affects live bridge processor behavior, the processor thread owns it.

That is much easier to review and test than a web of `volatile` fields and condition-variable style waits.

## Rollout Plan

### Step 0: Write This Plan

Done by this document.

Purpose:

- survive context compaction
- keep the design reviewable across multiple PRs
- make it easy to refine the plan before code moves

### Step 1: Introduce Processor Scaffolding

Add the processor-layer types and plumbing:

- processor thread
- event queue
- command/result plumbing
- `BridgeProcessorState` container
- dedicated processor package/directory with one class per file by default

At this step the goal is scaffolding, not semantic change.

### Step 2: Make Callback Handling Enqueue-Only

Change the callback listener path so `handleCallback()` stops being the place where processor-owned state is mutated directly.

Instead, it should:

- validate/decompress callback data
- build a `BridgeEvent`
- enqueue it for the processor
- keep callback dispatch/apply logic in processor-side classes, not in `BridgeCallbackHandler`

The important review constraint for steps 1-2:

- do not claim the shared-state model is gone yet
- do not try to partially reroute half the MCP methods in the same PR

This first PR should be understandable as "introduce the processor package and move callback ingestion onto it."

### Step 3: Move MCP Methods to Commands

Convert the core MCP methods to command/response calls into the processor:

- `pass_priority`
- `choose_action`
- `get_action_choices`
- `get_game_state`

Likely also:

- `executeDefaultAction`
- `concede`
- `send_chat_message`

These methods are the real cross-thread API boundary. Once they stop reading shared fields directly, the processor state can actually become single-owned.

This should likely be a second PR, shortly after steps 1-2, to keep review size manageable.

Important: this step may still use transitional long-running processor commands for
flows like `pass_priority` and `choose_action`. That is acceptable as an
intermediate state for correctness, but it is not the desired end state.

### Step 4: Remove Transitional Shared-State Machinery

After the command migration lands, delete the old synchronization model:

- `actionLock`
- `wait()/notifyAll()`-style pending-action loops
- extra `volatile` fields that only existed for cross-thread visibility
- temporary bridge code that mirrors old and new control flow
- long-running command handlers that block while "owning" the processor thread
- callback-pumping escape hatches such as `processNextCallback(...)` and deferred nested-command draining

Replace them with split-phase processor-owned requests:

- an MCP/tool thread submits a request and waits on a future
- the processor records that request in processor-owned state and returns to the normal event loop
- incoming callbacks advance the request state machine
- the processor completes the waiting future once the request reaches a real decision/result boundary

In the end state, MCP commands should not monopolize the processor thread while
waiting for future callbacks. The processor should remain in its normal event loop
and satisfy requests incrementally as events arrive.

This step should also delete transitional adapter seams created during step 3.
In particular, processor-side flows should not depend on large handler-owned
context adapters like `createPassPriorityFlowContext()`. If a processor flow
still needs a broad facade back into `BridgeCallbackHandler`, that is a sign the
underlying state, helper logic, or side-effect plumbing still lives in the
wrong place. Move that ownership into `mage.client.bridge.processor` and remove
the adapter instead of polishing it.

This step cashes in the simplification. It should shrink the handler and the processor core meaningfully.

#### Current checkpoint after the flow-lifecycle extraction PR

After the flow-lifecycle extraction work lands:

- callback ingress is enqueue-only
- `pass_priority`, `choose_action`, `get_action_choices`, and `get_game_state`
  already route through the processor
- pending decision state lives in `BridgeDecisionState`
- the old broad handler adapters (`createPassPriorityFlowContext()` /
  `createChooseActionFlowContext()`) are gone, replaced by dedicated manager
  and context classes
- callback-pumping escape hatches such as `processNextCallback(...)` and
  deferred nested-command draining are already deleted

That is a real architectural checkpoint. The next major boundary after it is
removing the remaining handler-owned processor state and helpers.

#### Current checkpoint after the caller-wait removal PR

After the caller-wait removal work lands:

- `choose_action` and `pass_priority` no longer use caller-driven
  `awaitResult(timeout)` polling loops in `BridgeCallbackHandler`
- the caller thread no longer drives progress by manually ticking flows or
  pumping callbacks
- caller-thread interruption no longer duplicates processor commands while
  trying to start or cancel a flow; the processor command handoff now preserves
  interrupt status across the mailbox round-trip
- `pass_priority` ticking is processor-owned via scheduled mailbox work rather
  than caller-owned timeout loops

That is another real architectural checkpoint, but it is still not the desired
end state.

The main remaining gaps are:

- too much processor-owned state and helper logic still lives in
  `BridgeCallbackHandler` (`currentGameId`, `lastGameView`, mana-plan state,
  turn counters, keepAlive lifecycle state, unseen chat, bridge-event cursors,
  and related helper methods)
- some MCP-facing reads, notably `isActionPending()`, still read transitional
  shared state directly instead of going through a processor-owned request or
  published view
- processor-side flows and services still reach back into handler-owned helpers
  more often than they should

#### Expected remaining PRs after the caller-wait removal PR

Recommended minimum:

- **2 required PRs** to finish the core processor refactor
- **1 optional PR** for step 5:
  published immutable snapshots / append-only log read model

In other words: after the caller-wait removal PR, expect **2 required PRs left**
for the core refactor, plus **1 optional followup PR** if the published read
model still looks worthwhile.

#### Recommended split of the remaining required work

Keep the remaining required work focused on one theme:

- move the rest of processor-owned state and helper logic out of
  `BridgeCallbackHandler`

Recommended cut:

- **PR D2a: Move remaining processor state into processor-local classes**
  Move the remaining game/lifecycle state into processor-local classes so the
  handler stops owning fields like `currentGameId`, `lastGameView`,
  active-game tracking, callback timestamps, and keepAlive latches directly.

  This PR should also rewire the flow contexts to read that processor-owned
  state directly instead of going back through handler pass-through methods.

- **PR D2b: Move remaining processor helper/service logic out of the handler**
  Move the rest of the processor-owned mutable state and helper logic into
  processor-local state/services so `BridgeCallbackHandler` becomes mostly the
  listener adapter plus MCP command wiring.

  This follow-up should cover, at minimum:
  - remaining decision-adjacent mutable state like mana-plan state, turn
    counters, loop detection, and failed-mana tracking
  - unread chat / bridge-event cursor state if it is still part of live bridge
    behavior
  - remaining package-private helper surfaces that only exist to let
    processor-side classes reach back into `BridgeCallbackHandler`

#### Current checkpoint after the game-state ownership PR

After the game-state ownership work lands:

- `BridgeGameState` owns game/lifecycle state like `currentGameId`,
  `currentPlayerId`, `lastGameView`, active-game tracking, callback timestamps,
  keepAlive latches, and related lifecycle flags
- the choose/pass flow contexts read that processor-owned state directly
  instead of going back through handler getter methods
- `BridgeCallbackHandler` is smaller, but it still owns interaction/mana state,
  chat/event-log state, cursor state, and too much helper logic

At that point there should be:

- **1 required PR** left for the core processor refactor (`D2b`)
- **1 optional PR** left for the published read model

### Step 5: Published Read Model / Append-Only Log

Separate followup.

Possible followup work:

- publish immutable processor snapshots from the processor
- maintain an append-only event log for MCP readers and debugging
- make read-only surfaces consume that published state instead of querying the processor directly

This is useful, but not required for the main correctness win. The critical improvement happens once state ownership and command routing are single-threaded.

## Why Split Steps 1-2 From Step 3

Doing steps 1-3 in one PR would likely be correct in spirit, but too large to review cleanly.

The biggest semantic risk is not the processor scaffolding itself. It is rewriting:

- `passPriority()`
- `chooseAction()`
- `getActionChoices()`
- `getGameState()`

to stop depending on shared mutable fields.

That argues for:

- **PR 1**: steps 1-2
- **PR 2**: step 3

The split is only worthwhile if PR 1 stays disciplined and clearly remains scaffolding plus callback-ingestion migration. It should not become a half-finished semantic rewrite.

## Review Strategy

### PR 1: Processor Scaffolding + Enqueue-Only Listener

Expected review focus:

- processor lifecycle
- event/command type shape
- callback ordering
- minimal behavior drift

### PR 2: MCP Command Migration

Expected review focus:

- semantic parity for `pass_priority`, `choose_action`, `get_action_choices`, `get_game_state`
- side-effect serialization
- removal of direct shared-state access from MCP threads

### Remaining PR 1: Finish Ownership Cleanup

Expected review focus:

- movement of processor-owned state/helpers out of `BridgeCallbackHandler`
- elimination of remaining handler-only helper facades needed by processor code
- whether direct reads like `isActionPending()` now have a cleaner ownership model

### Optional Followup: Published Read Model

Expected review focus:

- readability improvements
- optional snapshot/log publication model

## Open Questions

These do not block the overall direction, but should be resolved during implementation:

1. **One mailbox or two?**
   We can use one unified queue for both events and commands, or separate queues with a single processor loop multiplexing them.

2. **How should command replies work?**
   `CompletableFuture<BridgeCommandResult>` is the straightforward default.

3. **Should `sendPlayer*` be processor-only from the first migration?**
   The design intent says yes. If any helper still sends directly from another thread, the model is only partially fixed.

4. **How much keepAlive state moves in PR 1 versus PR 2?**
   Ideally the processor owns it, but we may need a short transitional layer.

5. **Do read-only tools become commands or published snapshots first?**
   Current preference: commands first for correctness, published read model later for architecture cleanliness.

## Acceptance Criteria For The First Real Milestone

After steps 1-3 land, we should be able to say:

- callback listener threads no longer mutate processor-owned state directly
- core MCP methods no longer read processor state directly from shared fields
- processor state has one logical owner thread
- `sendPlayer*` side effects are serialized through that owner
- the bridge no longer depends on `pendingActionReady`-style publication barriers for correctness

That is the point where this refactor has delivered real value, even before the append-only read model followup.
