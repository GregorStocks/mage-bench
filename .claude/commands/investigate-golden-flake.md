# Investigate Golden Flake

Debug a golden test failure from CI logs, identify the root cause, and fix it.

**Input:** The user provides a GitHub Actions URL (run or job link) for a failing golden test.

## Step 1: Get the CI failure details

Extract the run ID and job ID from the URL and fetch the failed logs:
```bash
gh run view <run_id> --job <job_id> --log-failed
```

If the user gave a run URL without a job ID, list jobs first:
```bash
gh run view <run_id> --json jobs --jq '.jobs[] | "\(.databaseId) \(.name) \(.conclusion)"'
```

Identify from the logs:
1. **Which golden test failed** (e.g., `test_golden_dark_depths_combo`)
2. **The diff output** — the `_json_diff` lines showing expected vs. actual values
3. **What phase failed** — prompt comparison (`assert_golden_prompt`) or export comparison (`assert_golden_export`)
4. **The RPC trace** — the `[RPC #N] -> tools/call(...)` lines show the exact sequence of MCP tool calls

## Key invariant: priority serialization

**The XMage server serializes priority — only one player has it at a time.** A bridge/LLM only becomes active when its player has priority. Both bridges can NEVER be active simultaneously. This means:

- **Game replay MUST be deterministic.** Given the same deck and the same sequence of player decisions, the game state progression is fully determined. If a golden test produces different results across runs, that's a **bug in the Java bridge code**, not an inherent property of the system.
- **Never modify test scripts to work around nondeterminism.** Changing `pass_priority()` to `pass_priority(until="precombat_main")` or adding `my_turn` yields to "make it deterministic" is papering over a bug. The plain `pass_priority()` should already be deterministic because only one bridge processes callbacks at a time.
- **The fix is always in the Java bridge.** If the `firstPass` logic, `actionsPassed` counter, or auto-pass loop produces different results across runs, something is violating the priority serialization invariant. Find where and fix it.

## Step 2: Classify the failure

Golden flakes fall into a few known categories. Match the diff against these patterns:

### Race conditions (timing-dependent)
- **`game_seq` drift**: Same payload, different `game_seq` values. Caused by `lastGameView` being updated from asynchronous `gameUpdate` callbacks instead of the authoritative callback. Look at `updateLastGameView()` call sites in `BridgeCallbackHandler.java`.
- **Empty `get_game_history`**: `cursor: N -> 0`, `event_count: N -> 0`, history becomes `"No game events recorded yet."`. Race between `handleGameOver()` cache population and Python's post-script `get_game_history` call.
- **`bridge_join` timeout**: Timeout waiting for bridge/potato to join the table. Usually a `keepAlive` loop issue where the previous game's cleanup races with the next game's setup. Check `gameFinishedLatch`, `handleGameOver`, and `client.stop()` ordering.
- **Missing or extra callbacks**: `pass_priority` or `choose_action` returns different results. The bridge auto-pass loop (`actionsPassed` counter, `firstPass` logic) saw a different number of server callbacks. This violates the priority serialization invariant — find where the bridge is seeing callbacks out of order or dropping/duplicating them.

### Nondeterministic ordering
- **`llmEvents` / `llmTrace` order**: Events at the same `seq` for different players interleave differently. Check if `_strip_volatile` sorts by `(seq, player)`.
- **Embedded JSON key order**: Same data, different serialization order. Check if `_normalize_embedded_json` is being applied.
- **Short-ID churn**: Same cards get different `pN` IDs across runs. Check `ShortIdRegistry` and whether IDs are assigned in a deterministic order.

### Logic bugs exposed by timing
- **Auto-pass behavior change**: A `pass_priority` call passes more or fewer times than expected. Check the `firstPass` optimization, `yieldUntil*` flags, and `playable` filtering in `passPriority()`.
- **Stale response**: A callback gets answered by a response meant for a different callback. Look for `skip()`, `waitResponseOpen()`, and `sendPlayerBoolean()` ordering issues.

## Step 3: Read the relevant code

Based on the classification, read the key files. Almost all golden flakes trace back to:

- **`Mage.Client.Bridge/src/main/java/mage/client/bridge/BridgeCallbackHandler.java`** — the central 5000+ line file. Key sections:
  - `passPriority()` (~line 3019): The main priority-passing loop with auto-pass logic
  - `handleGameOver()` (~line 5382): Game cleanup, bridge event caching, `activeGames` removal
  - `pullBridgeEvents()` (~line 2709): Pulls events from server, falls back to cache
  - `getGameHistory()` (~line 2737): Formats bridge events into human-readable history
  - `handleGameUpdate()`: Asynchronous game state updates from the server

- **`puppeteer/tests/golden_helpers.py`** — test infrastructure:
  - `run_golden_scenario()` (~line 611): Orchestrates the full test (spectator, bridges, replay, comparison)
  - `_strip_volatile()` (~line 993): Removes timing-dependent fields from export data
  - `_normalize_prompt_for_golden()` (~line 924): Normalizes prompt data (strips short IDs, sorts JSON)
  - `assert_golden_export()` (~line 1022): Export comparison with normalization
  - `assert_golden_prompt()` (~line 949): Prompt comparison

- **`puppeteer/src/puppeteer/replay.py`** — replay script execution:
  - `execute_replay_script()`: Runs the scripted tool calls, captures post-script `get_game_state` + `get_game_history`

- **`puppeteer/tests/conftest.py`** — fixtures for XMage server, bridge sessions, spectator process

Also read the **specific golden test file** (`puppeteer/tests/test_golden_<name>.py`) and its **golden files** (`puppeteer/tests/golden/prompts/<name>.json` and `puppeteer/tests/golden/exports/<name>.json`).

## Step 4: Trace the bug

Follow the execution path to understand what happened:

1. **Read the diff carefully.** The `[N].content.field: expected -> actual` format tells you exactly which message in the prompt/export array changed and which field diverged.
2. **Cross-reference with the RPC trace.** The `[RPC #N]` lines in the test stdout map to the message array indices. Find the RPC that produced the divergent result.
3. **Identify the threading model.** Golden tests run two replay bridges + a spectator concurrently. Player A and Player B each run on their own thread, and the XMage server fires callbacks on a separate callback thread. Identify which threads are involved in the race.
4. **Check ordering constraints.** Most races are about operations happening in the wrong order. Look for:
   - `activeGames.remove()` vs cache population (handleGameOver)
   - `actionLock.notifyAll()` vs state updates
   - `client.stop()` vs pending RPCs
   - `gameFinishedLatch.countDown()` vs next game setup

## Step 5: Fix the root cause

**Critical rules from AGENTS.md:**
- **Never add graceful fallbacks or silent defaults.** If the data should be there, fix the ordering so it IS there. Don't add `if empty: wait` or `if null: return default`.
- **Never strip a field from `_strip_volatile`** to mask the flake. `_strip_volatile` is for fields that are inherently volatile (timestamps, wall-clock durations). If a field like `game_seq` is nondeterministic, fix the source of nondeterminism.
- **Never re-run CI to work around a failure.** The flake has a root cause — find it.

Common fix patterns:
- **Reorder operations**: Move state population before the signal that makes it visible to other threads (e.g., populate cache before removing from activeGames).
- **Use the authoritative callback**: The XMage server sends specific callbacks (game-over, game-update) with authoritative state. Use that state instead of racing with asynchronous pushes.
- **Fix the bridge's callback handling**: If the auto-pass loop sees different numbers of callbacks across runs, the bridge is violating the priority serialization invariant. Look for places where callbacks are dropped, duplicated, or processed out of order (e.g., `pendingAction` being overwritten, `actionLock` contention, `GAME_UPDATE` callbacks interfering with priority callbacks).

**Never work around nondeterminism by modifying test scripts.** If `pass_priority()` is nondeterministic, the fix is in the Java bridge, not in switching to `pass_priority(until="precombat_main")`. See "Key invariant: priority serialization" above.

## Step 6: Verify the fix

1. `make build` — ensure Java compiles
2. `make check` — lint, typecheck, unit tests
3. Run the specific golden test multiple times:
   ```bash
   for i in 1 2 3 4 5; do echo "=== Run $i ==="; xvfb-run make test-golden K=<test_name> 2>&1 | grep -E 'PASSED|FAILED|Error'; done
   ```
   All runs should pass. A flake fix that doesn't survive 5 consecutive local runs isn't a fix.

## Step 7: Commit and PR

Use `/pr` to create the pull request. The PR description should include:
- The root cause (which threads raced, what the ordering issue was)
- The fix (what was reordered/changed and why)
- That local golden tests passed N/N consecutive runs
