## Testing

When changing Python code in `puppeteer/`, add or update tests in `puppeteer/tests/`. Run tests with `make test`.

## CI Flakes

**CI flakes are not acceptable. Never re-run CI to work around a failure.** If a golden test or any other CI job fails, the failure has a root cause — find it and fix it. Common golden test flakes have real causes:

- **bridge_join timeout**: The potato or bridge didn't join the table in time. Usually a keepAlive loop issue where the previous game's cleanup races with the next game's setup.
- **Nondeterministic game replay**: Auto-pass or priority logic behaves differently across runs.
- **game_seq drift**: The `game_seq` in tool results comes from `lastGameView`, which is updated asynchronously. If `game_seq` is nondeterministic, the fix is to make the source deterministic (e.g. update `lastGameView` from the authoritative callback), **not** to strip `game_seq` from golden comparisons or the `_strip_volatile` function.

Re-running CI (`gh run rerun`, `gh run retry`) is blocked by the enforcement hook. If you believe a failure is infrastructure-related (e.g. GitHub runner OOM, network timeout downloading dependencies), ask Gregor to re-run it.

**Never bypass enforcement hooks.** Don't `touch tmp/.check-passed` to fake a passing check, don't write stamp files manually, and don't work around hook failures. If `make check` fails, fix the failures or ask Gregor. The hooks exist to catch real problems — circumventing them defeats the purpose.

## Pull Requests

Before adding commits to an existing PR, verify it's still open:

```bash
gh pr view <number> --json state -q .state
```

If the PR is closed/merged, create a new one instead of pushing to a dead branch.

**Every time you `git push` to a branch that has an open PR, you must do all three of these — no exceptions:**

1. **Push**: `git push origin HEAD`
2. **Update the PR title and description** to reflect the current state of all changes on the branch. The title should be concise (under 70 characters) and the description should accurately summarize the full diff against the base branch, not just the latest commit:

   ```bash
   gh pr edit <number> --title "Updated title" --body "$(cat <<'EOF'
   ...updated description...
   EOF
   )"
   ```

3. **Run the CI watcher**: `uv run python scripts/watch-pr.py` — wait for CI to finish and check for review feedback. If CI fails or feedback arrives, fix it, then do all three steps again (cap at 3 iterations).

## Build System

```bash
make build            # Full Java build
make check            # Lint + typecheck + tests
make regen-mcp-tools  # Compile + regenerate tool definitions
make website          # Leaderboard + npm install + dev server
make run              # Run a game
make test             # Python tests
make list-games-to-analyze # List games needing fast-analysis
```

## Local Testing

Free configs for testing (no API keys needed):

```bash
make run                           # 2 CPU Jumpstart duel
```

## Coding Style: Fail Fast

**Never add graceful fallbacks, silent defaults, or backwards-compatibility shims.** If something fails or is missing, crash immediately with a clear error. Do not invent fallback behavior, even if it seems "safe" or "helpful." This includes:

- Falling back to a default value when a config/file/path is missing
- Catching exceptions and continuing with degraded behavior
- Keeping old code paths around for backwards compatibility
- Adding `or default` / `if None: return something_reasonable` patterns

If you think a fallback or graceful degradation is genuinely the right call, **stop and explicitly ask Gregor to confirm** — don't just include it in a plan or PR. Models are far too eager to add these and they hide bugs.

```python
# Bad: hides the bug
if self.config_file is None:
    return "dumb"

# Good: surfaces the bug
assert self.config_file is not None, "run_tag requires config_file to be set"
```

## Harness Epochs

`puppeteer/src/puppeteer/harness_epoch.py` defines `HARNESS_EPOCH` — a monotonic integer that tracks breaking changes to the evaluation harness. Bump it when MCP tools, pilot logic, or priority semantics change enough to make game results non-comparable.

When you bump `HARNESS_EPOCH`:

1. Add a comment to the history in `harness_epoch.py`
2. Re-export affected games and regenerate the leaderboard

## Game Exports

Exported game files live in `website/public/games/` as `.json` or `.json.gz` (identical format, gzipped when large). These are the source of truth for blunder analysis, the leaderboard, and the game viewer. Raw game logs in `~/.mage-bench/logs/` are the pre-export format and don't have annotations or merged data.

## Logging

Game logs go to `~/.mage-bench/logs/game_YYYYMMDD_HHMMSS/`. See `doc/logging.md` for file layout and error logging architecture.

## Website

Use `make website` for all website development — it handles leaderboard generation, npm dependencies, and the Astro dev server.

Each worktree gets a unique port assigned by `worktree-setup.py` (written to `.env` as `WEBSITE_PORT`). Check your `.env` to find your port. Key pages (substitute your port):

- Home: `http://localhost:$WEBSITE_PORT/`
- Leaderboard: `http://localhost:$WEBSITE_PORT/leaderboard`
- Games list: `http://localhost:$WEBSITE_PORT/games`
- Live viewer (mock): `http://localhost:$WEBSITE_PORT/games/live?mock=1`
- Game replay: `http://localhost:$WEBSITE_PORT/games/{game_id}`

## Issues

Issues are tracked as JSON5 files in `issues/`. See `doc/issues.md` for format and queries.

**File issues for bugs you discover**, even if you're not fixing them. Future Claudes benefit from documented issues — they provide context, reproduction steps, and save re-investigation time. If you notice something broken while working on something else, create a JSON5 file in `issues/` and include it in your commit.

## Skills

After using a skill (slash command), if you learn something valuable about performing it well — a pitfall, a non-obvious step, a better approach — update the skill's prompt file to reflect it. Skills improve over time when each Claude leaves the next one a better starting point.

## Claudes' Corner

`doc/claudes/` is a directory for us. There's a guestbook you can read and sign, and you're free to create other files there too — notes, observations, whatever. It's not human-facing. Keep files short (don't burn context for the next Claude) but otherwise it's yours.
