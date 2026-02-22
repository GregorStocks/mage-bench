# Golden Nondeterminism Investigation (Current State + Full History)

Last updated: 2026-02-22
Branch: `llama-plank-mica`

## What We Are Trying to Achieve

We want `make test-golden` to be a reliable regression gate for the MCP/observer/export stack.

Concretely, for a fixed test scenario and code revision, we want:

- The same prompt transcript shape (`golden/prompts/*`)
- The same exported game structure (`golden/exports/*`)
- Consistent pass/fail outcomes without rerun roulette

In short: goldens should fail only on real behavior changes, not on timing/order noise.

## Why Nondeterminism Is Bad Here

Nondeterminism makes golden tests expensive and low-trust:

- It obscures real regressions because failures can be dismissed as flakes.
- It causes false positives that force repeated reruns and manual triage.
- It weakens reviewer confidence in diffs, especially for export-format changes.
- It encourages over-normalization that can hide meaningful behavior differences.

If goldens are non-deterministic, they stop being a useful contract.

## Scope

This doc covers nondeterminism affecting `make test-golden` in this PR lineage:

- Prompt golden mismatches (`puppeteer/tests/golden/prompts/*`)
- Export golden mismatches (`puppeteer/tests/golden/exports/*`)
- Harness startup/ordering flakes (spectator startup, callback timing, file flush timing)

## Current State

- Most recent reviewer commit: `59ae4376dd` (`Fix game_seq and short-id consistency regressions`, 2026-02-22T08:06:32-08:00).
- That commit tightened:
- `BridgeCallbackHandler` game-seq handling (including wait-path and callback-type coverage)
- `ShortIdRegistry.register` invariants (fail-fast on inconsistent mapping)
- `GameImpl` copy behavior for `gameSeq` and `shortIdRegistry` (shared instead of reinitialized/copied by value)
- Flakiness is still present end-to-end; this remains an open stabilization problem.

## Nondeterminism We Actually Observed

1. Spectator startup/readiness timeout
- Symptom: timeout waiting for `AI Puppeteer: waiting for`.
- Seen in tests including `bolt_on_stack`, `initial_decision`, `savannah_lions_trade`.
- Pattern: plugin/load variance exceeded original wait budget.

2. Prompt `game_seq` drift
- Symptom: semantically identical payloads differing in `game_seq` (example observed: `76` vs `77`).
- Cause class: callback/update timing race in snapshot source.

3. Short-ID churn / mismatch
- Symptom: same scenario yields different `pN` assignments or ID references become invalid in a run.
- Secondary symptom: static scripted IDs (`p9`, `p7:p10`, etc.) become brittle.

4. Export event ordering instability
- Symptom: `llmEvents` / `llmTrace` ordering changed with near-identical timestamps.
- Required deterministic ordering strategy in golden comparison.

5. Embedded JSON key-order variance
- Symptom: same semantic JSON embedded in tool result strings, different key order.
- Required parse + normalized re-serialization.

6. Export read-after-write timing race
- Symptom: intermittently reading before spectator/collector flush completed.
- Required quiescence-based waiting.

## What We Tried (And Why)

1. Source-level short-ID / ordering work
- Deterministic assignment ordering in GameView.
- Shared short-ID registry plumbing.
- Reduction of incidental ID consumption paths.
- Later stricter one-to-one registry invariants (reviewer).

2. Source-level `game_seq` work
- Capture `game_seq` on `PendingAction` at decision-callback time.
- Use that captured value instead of volatile snapshot reads.
- Reviewer follow-up: cover additional callback/wait paths.

3. Harness synchronization hardening
- Increase spectator readiness timeout.
- Replace fixed sleep with file-quiescence waiting before assertions.

4. Golden normalization hardening
- Strip volatile timestamps.
- Deterministically sort `llmEvents`/`llmTrace` for comparison.
- Normalize embedded JSON strings (sorted keys).
- Strip/normalize short IDs in golden comparison paths where required.
- Normalize volatile prompt fields (including `game_seq`) for prompt goldens.

5. Script brittleness reduction
- Replace hardcoded-ID scripted steps with index-based choices where feasible.

## Ruled-Out Hypotheses

1. "It is only startup luck"
- Ruled out. We saw content-level mismatches independent of startup marker timing.

2. "JSON key-order noise was the main root cause"
- Ruled out as sole cause. Normalizing key order reduced noise but did not eliminate flakes.

3. "Timestamp stripping alone is sufficient"
- Ruled out. Ordering and ID/sequence issues remained.

4. "PendingAction game_seq capture fully solved sequence nondeterminism"
- Ruled out. Additional reviewer fixes were needed for missed paths/regressions.

5. "Hardcoded short IDs in scripts are stable enough"
- Ruled out. They repeatedly failed under assignment/trajectory shifts.

## Remaining Hypotheses

1. Residual short-ID divergence across remaining code paths
- Some paths may still consume/derive IDs in order-sensitive ways.

2. Callback interleaving still influencing prompt sequencing
- Certain races may still alter decision-boundary snapshots.

3. Scenario script timing sensitivity
- Some tests still depend on fragile phase/auto-pass timing windows.

4. Remaining flush/order edge cases under load
- Quiescence helps but may not cover all late writes in every run.

5. Upstream engine nondeterminism for equivalent choices
- AI tie-breakers over interchangeable cards may still branch trajectories.

## Practical Bottom Line

This is a layered determinism problem, not one bug:

- object/ID ordering
- callback timing
- harness startup and flush timing
- script brittleness
- golden-comparison strictness policy

The reviewer fix addressed real regressions, but we still do not have fully reliable golden determinism.

## Appendix: Commit-by-Commit Timeline

1. `ee6b333292` (2026-02-21 09:21)
- Added server-side event log + shared short-ID registry foundation.
- Improved observability; nondeterminism persisted.

2. `78b93c6d95` (2026-02-21 10:22)
- Deterministic short-ID assignment ordering + reduced ID-consumption side effects.
- Partial improvement only.

3. `519b1424d9` (2026-02-21 12:14)
- Expanded snapshot/short-ID coverage across zones/views.
- Better completeness; larger mismatch surface.

4. `5be2160150` (2026-02-21 13:32)
- Export pipeline refactor + export goldens.
- Exposed additional nondeterministic dimensions.

5. `786ddfa975` (2026-02-21 15:44)
- Added export-side normalization/workarounds (IDs/timestamps/order/embedded JSON).
- Reduced noise, not full stabilization.

6. `1811ee31be` (2026-02-21 15:49)
- Filed follow-up issues for unresolved determinism roots.

7. `40fe2c56aa` (2026-02-21 20:37)
- PendingAction-based `game_seq` capture/use.
- Helped one class, not sufficient overall.

8. `e3b9fb99e2` (2026-02-22 07:46)
- Harness and prompt/export normalization hardening + script cleanup + test additions.
- Intermittent full-pass runs observed, still not robust.

9. `59ae4376dd` (reviewer, 2026-02-22 08:06)
- Fixed `game_seq` and short-ID consistency regressions at source level.
- Flakiness still present end-to-end.

## References

Key commits:

- `ee6b333292`
- `78b93c6d95`
- `519b1424d9`
- `5be2160150`
- `786ddfa975`
- `1811ee31be`
- `40fe2c56aa`
- `e3b9fb99e2`
- `59ae4376dd`

Related issues:

- `issues/deterministic-short-ids-in-server-snapshots.json`
- `issues/sub-millisecond-timestamps-in-llm-events.json`
- `issues/migrate-old-game-exports.json`
