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

#### Current checkpoint after the batch-combat `choose_action` flow

After the split-phase batch-combat `choose_action` work lands, the last
processor-owning wait loop should be gone:

- batch attackers/blockers no longer block inside `BridgeCallbackHandler`
- `choose_action` no longer has a separate blocking implementation
- `BridgeProcessor.processNextCallback(...)` and deferred nested-command
  draining should be deleted

At that point, the processor refactor is past the behavioral transition. The
main remaining work is structural cleanup: too much processor-owned state and
helper logic still lives in `BridgeCallbackHandler`, and the processor-side
flows still reach back through broad context adapters.

#### Expected remaining PRs after the batch-combat `choose_action` PR

Recommended minimum:

- **One required PR** to finish step 4's structural cleanup:
  move remaining processor-owned state/helpers out of
  `BridgeCallbackHandler`, remove large adapters like
  `createPassPriorityFlowContext()` / `createChooseActionFlowContext()`, and
  delete leftover synchronization/notification plumbing that only exists for
  the old shared-state model.
- **One optional PR** for step 5:
  published immutable snapshots / append-only log read model.

In other words: after the batch-combat `choose_action` PR, expect
**1 required PR** to finish the core processor refactor, plus
**1 optional followup PR** if the published read model still looks worthwhile.

#### Recommended split of the remaining required work

Keep the remaining required work focused on ownership cleanup, not behavior
changes.

Recommended cut:

- **PR C: Move processor ownership out of the handler**
  Introduce a real processor-owned state/service boundary so flows stop
  depending on broad handler facades. Move the remaining processor-owned helper
  logic and mutable state out of `BridgeCallbackHandler`, then delete adapters
  like `createPassPriorityFlowContext()` and `createChooseActionFlowContext()`.
- **PR C (same PR if reviewable, or followup cleanup commit):**
  remove any leftover `actionLock` notifications, extra `volatile` fields, and
  similar transitional synchronization that no longer has a real waiter or
  cross-thread consumer.

If PR C turns out too large in review, split it again:

- **PR C1:** move processor-owned state/helpers out of `BridgeCallbackHandler`
- **PR C2:** delete the now-obsolete synchronization/notification scaffolding

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

### PR 3: Finish Step 4

Expected review focus:

- semantic parity for batch-combat `choose_action`
- removal of processor-thread wait ownership from the last remaining MCP flow
- confirmation that callback-pumping escape hatches are now dead code or close
  to it

### PR 4: Structural Cleanup

Expected review focus:

- deletion of obsolete synchronization and callback-pumping helpers
- movement of processor-owned state/helpers out of `BridgeCallbackHandler`
- removal of large handler-to-processor context adapters

### PR 5+: Published Read Model

Expected review focus:

- deletion of obsolete synchronization
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
