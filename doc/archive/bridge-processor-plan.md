# Bridge Processor Plan (Archived)

**Status: Complete.** All goals achieved. Archived 2026-03-25.

## Goal

The goal was a real actor-style bridge runtime where:

- the XMage listener thread only enqueues immutable events onto the processor queue
- the processor thread is the only thread allowed to mutate live bridge runtime state
- MCP/tool threads only enqueue commands or read immutable data published by the processor
- `sendPlayer*` side effects are issued only by the processor thread
- correctness no longer depends on shared `volatile` fields, `synchronized`
  state, latches, or ad hoc compare-and-swap style shared-memory protocols

## Why It Mattered

The bridge had recurring golden-test flakes and cleanup races caused by
shared-memory coordination. The single-writer processor model was adopted to
make ordering and ownership explicit enough that race conditions stop being a
normal failure mode.

## Architecture (as implemented)

### Threads

1. **listener** (`bridge-listener-*`)
   Receives XMage callbacks, decompresses/normalizes them, and enqueues
   immutable `BridgeCallbackEvent` records. Holds no state between callbacks.

2. **processor** (`BridgeProcessor`)
   Owns all live bridge runtime state via `BridgeProcessorState`. Processes
   both callbacks (via `BridgeCallbackProcessorService`) and MCP commands
   (via `BridgeActionCommandService`). Only thread that mutates state or sends
   XMage responses. Thread ownership enforced by `isProcessorThread()` checks.

3. **mcp** (`BridgeMcpActionApi` / `BridgeMcpQueryApi`)
   Actions: submit commands to the processor and await results.
   Reads: sync via listener/processor barrier, then read immutable published
   snapshots (`BridgePublishedQuerySnapshot`).

### Data model

- Processor-owned append-only game log with monotonic cursors
- Immutable published snapshots for game state, action choices, oracle index
- Processor-side projections at authoritative update points
- `Bridge*State` holders (`BridgeDecisionState`, `BridgeGameState`,
  `BridgeInteractionState`, `BridgeGameLogState`, `BridgeCursorState`) are
  plain single-threaded processor internals under `BridgeProcessorState`
- `BridgeProcessorServices` is a thin processor-private services layer
- MCP naming: `snapshot_id` for snapshot reads, `cursor` for log/history

### Cross-thread publication

The only remaining volatile fields are genuinely needed for cross-thread
publication, not remnants of the old async model:

- `session` on `BridgeCallbackHandler` (multi-threaded `setSession()`)
- `publishedDecklist` (cross-thread MCP read)
- `joinHandler` (external MCP call)
- `publishedSnapshot` / `publishedOracleIndex` on `BridgePublishedQueryState`
- Config volatiles (`keepAliveAfterGameConfig`, `maxInteractionsPerTurnConfig`,
  `errorLogPath`, `bridgeLogPath`)
- `BridgeProcessor.closed`, flow manager lifecycle flags

`synchronized` blocks exist only in flow managers for timeout/tick
coordination, not for protecting mutable processor state.

## What was deleted

- `BridgeGameLogRefresher` (async fetch executor, sync epoch barriers, syncLock)
- `Session.getBridgeEvents()` RPC chain end-to-end
  (`SessionImpl` -> `MageServerImpl` -> `GameManagerImpl` -> `GameController`)
- `bridgeEventCache`
- `gameFinishedLatch`
- Old instance-based `BridgeProcessorServices.getOracleText()`
- Mutable `deckListSupplier` on `BridgePublishedQueryBuilder`
- Direct `processorState` reach-backs from query builders
- `ConcurrentHashMap`, `CopyOnWriteArrayList`, and shared-memory APIs on
  processor-owned state bags

## Definition of Done (all satisfied)

- Listener code is enqueue-only (decompresses then enqueues immutable events)
- MCP code enqueues commands or reads published immutable data
- All live runtime mutation happens on the processor thread
- No non-processor thread reads live mutable runtime state directly
- `BridgeCallbackHandler` no longer hides cross-thread ownership
- MCP naming reflects the actual model (snapshot ids, cursors)
- Shared-memory race flakes eliminated; async coordination machinery deleted
