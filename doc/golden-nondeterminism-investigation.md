# Golden Nondeterminism Investigation (Current State + Full History)

Last updated: 2026-02-22
Branch: `llama-plank-mica`

## Scope

This document covers nondeterminism affecting `make test-golden` in this PR lineage, including:

- Prompt golden mismatches (`puppeteer/tests/golden/prompts/*`)
- Export golden mismatches (`puppeteer/tests/golden/exports/*`)
- Harness-level startup/ordering flakes (spectator startup, callback timing)

It summarizes what we changed, what nondeterminism we observed in real runs, and what hypotheses are still open vs ruled out.

## Current State

- The most recent reviewer commit is `59ae4376dd` (`Fix game_seq and short-id consistency regressions`, 2026-02-22T08:06:32-08:00).
- That commit tightened three areas:
- `BridgeCallbackHandler`: game_seq is re-injected after wait in `choose_action`, and GameView extraction now handles both `GameClientMessage` and `AbilityPickerView`.
- `ShortIdRegistry.register`: now enforces one-to-one mapping and fails fast on inconsistent/racy remaps.
- `GameImpl`: `gameSeq` and `shortIdRegistry` are shared across copies instead of copied-by-value/reinitialized.
- Flakiness is not resolved end-to-end yet. In the latest in-progress check on current HEAD (stopped early by request), `test_golden_clone_copies_memnite` and `test_golden_dark_depths_combo` were already failing.

## Timeline of Changes and Observed Effects

1. `ee6b333292` (2026-02-21 09:21)
- Added server-side event log and shared short ID registry concept.
- Intent: deterministic server timeline (`gameSeq`) and consistent IDs across streams.
- Result: improved observability, but did not eliminate golden nondeterminism.

2. `78b93c6d95` (2026-02-21 10:22)
- Sorted GameView short-id assignment by card name; removed some ID-consuming paths in server collector.
- Hypothesis: non-deterministic iteration/early ID consumption was causing ID churn.
- Result: partial improvement only.

3. `519b1424d9` (2026-02-21 12:14)
- Expanded snapshots and short-id coverage across many zones/views.
- Result: better data completeness, but larger state surface increased mismatch opportunities.

4. `5be2160150` (2026-02-21 13:32)
- Switched export pipeline to server-log-first with legacy fallback and added export goldens.
- Result: exposed more nondeterminism (especially in exported structure ordering/IDs/events).

5. `786ddfa975` (2026-02-21 15:44)
- Added test-side normalization/workarounds:
- strip short IDs in export comparisons
- normalize embedded JSON key order
- strip timestamps and deterministically sort llmEvents/llmTrace
- Result: reduced noise; still not fully stable.

6. `1811ee31be` (2026-02-21 15:49)
- Filed issues for unresolved root causes:
- deterministic short IDs in snapshots
- sub-millisecond ordering for LLM events

7. `40fe2c56aa` (2026-02-21 20:37)
- Captured `game_seq` on `PendingAction` (decision callback time) instead of volatile `lastGameView`.
- Removed `game_seq` stripping in tests, expecting determinism.
- Observed effect: helped one failure mode, but not sufficient.

8. `e3b9fb99e2` (2026-02-22 07:46)
- Additional stabilizers:
- longer spectator startup timeout (240s)
- file-quiescence wait instead of fixed sleep
- prompt normalization for short IDs + volatile `game_seq`
- embedded JSON normalization also strips short IDs
- brittle hardcoded-ID scripts converted to index-driven where possible
- added normalization unit tests
- Observed effect in repeated local runs: periods of full pass, but not sustained as fully fixed.

9. `59ae4376dd` (reviewer, 2026-02-22 08:06)
- Fixed regressions around `game_seq`/short-ID consistency at source level.
- Current evidence: suite remains flaky (early failures still seen in latest run).

## Nondeterminism Experienced (Observed)

1. Spectator startup/readiness flake
- Symptom: timeout waiting for marker `AI Puppeteer: waiting for`.
- Affected tests seen: `bolt_on_stack`, `initial_decision`, `savannah_lions_trade`.
- Pattern: plugin loading latency can exceed the original 120s budget.

2. `game_seq` drift in prompts
- Symptom: semantically identical payloads differ only in `game_seq` (example observed: 76 vs 77).
- Root behavior: callback/update timing races around when snapshot is read.

3. Short-ID churn / mismatch
- Symptom: identical scenarios produce different `pN` assignments, or scripts refer to IDs that are not valid in that run context.
- Secondary symptom: hardcoded script IDs (`p7:p10`, `p9`, etc.) become brittle under shifted assignment order.

4. Event ordering instability in exports
- Symptom: llmEvents/llmTrace ordering unstable when timestamps are near-identical.
- Test workaround required deterministic content sort after volatile field stripping.

5. Embedded JSON key-order variance
- Symptom: tool result strings with same semantic JSON but different key order.
- Required parse-and-reserialize normalization.

6. Export write timing races
- Symptom: reading files before spectator/collector flush completes can produce intermittent mismatch.
- Mitigation needed quiescence-based wait.

## What We Tried to Remove Nondeterminism

1. Source-level ordering and ID assignment controls
- GameView short-id assignment sorting
- shared short-id registry propagation
- reduced accidental ID consumption paths
- stricter register semantics (reviewer)

2. Source-level `game_seq` stabilization
- `PendingAction.gameSeq` capture/use
- broader GameView extraction in callbacks (reviewer)
- re-inject `game_seq` after blocking wait paths (reviewer)

3. Harness/test synchronization hardening
- increased spectator readiness timeout
- wait-for-files-quiescent instead of fixed sleep

4. Golden normalization and comparison hardening
- strip volatile timestamps
- deterministic sort of llmEvents/llmTrace
- strip short IDs in export-golden path
- normalize embedded JSON strings
- normalize prompt volatile fields (`game_seq`) and short IDs

5. Script brittleness reduction
- replaced static short-ID targeting in flaky scenarios with index-based steps where feasible

## Ruled-Out Hypotheses

1. "It is only startup luck"
- Ruled out. We observed content mismatches independent of startup timeout (e.g., `game_seq` drift and prompt flow divergence).

2. "JSON key-order noise is the main remaining cause"
- Ruled out as sole cause. Key-order normalization helped but flakes persisted.

3. "Timestamp stripping alone is enough"
- Ruled out. Event ordering still needed deterministic sort, and other mismatch classes remained.

4. "Capturing `game_seq` from callback fully solves sequence nondeterminism"
- Ruled out. Additional regressions required reviewer fixes around callback types and wait paths.

5. "Static short IDs in scripts are stable enough"
- Ruled out. Hardcoded IDs repeatedly broke due assignment shifts and phase/context differences.

## Remaining Hypotheses (Open)

1. Residual short-ID divergence across code paths
- Even with stricter register semantics, some paths may still derive/consume IDs in different orders between runs.

2. Multi-thread timing windows still leaking into prompt sequencing
- Certain callback/action interleavings may still change decision boundary snapshots.

3. Script/action timing sensitivity
- Some scenarios may still depend on exact phase transitions or latent auto-pass behavior, causing branchy transcript differences.

4. Collector flush/ordering edge cases under load
- Quiescence wait helps but may not cover all late-arriving event files in every case.

5. Upstream engine nondeterminism with interchangeable cards
- Underlying AI tie-breakers for equivalent options may still create trajectory divergence not fully abstracted by current normalizations.

## Practical Conclusion

- We are no longer dealing with one bug; this has been a layered nondeterminism problem spanning:
- XMage object/action ordering
- callback timing and snapshot capture
- harness startup and file flush timing
- brittle test scripts
- golden comparison strictness policy
- The reviewer commit fixed real regressions in source-level consistency, but the system is still flaky in practice based on the latest run evidence.
- The two strongest unresolved clusters are still:
- short-ID/trajectory instability
- callback sequencing/timing sensitivity in decision transcripts

## References

Key commits in this investigation chain:

- `ee6b333292` Add server-side game event log
- `78b93c6d95` Fix short ID assignment determinism
- `519b1424d9` Comprehensive snapshots + short IDs
- `5be2160150` Server-side export pipeline + export goldens
- `786ddfa975` Golden determinism normalizations/workarounds
- `1811ee31be` File nondeterminism issues
- `40fe2c56aa` game_seq capture on PendingAction
- `e3b9fb99e2` Stabilize goldens + script hardening
- `59ae4376dd` Reviewer fix for game_seq/short-id regressions

Related issue files:

- `issues/deterministic-short-ids-in-server-snapshots.json`
- `issues/sub-millisecond-timestamps-in-llm-events.json`
- `issues/migrate-old-game-exports.json`
