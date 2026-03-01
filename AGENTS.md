## Testing

When changing Python code in `puppeteer/`, add or update tests in `puppeteer/tests/`. Run tests with `make test`.

## Pull Requests

Before adding commits to an existing PR, verify it's still open:

```bash
gh pr view <number> --json state -q .state
```

If the PR is closed/merged, create a new one instead of pushing to a dead branch.

When pushing new commits to a branch with an open PR, update the PR description to reflect the current state of the changes:

```bash
gh pr edit <number> --body "$(cat <<'EOF'
...updated description...
EOF
)"
```

## Build System

```bash
make build            # Full Java build
make check            # Lint + typecheck + tests
make mcp-tools        # Compile + regenerate tool definitions
make website          # Leaderboard + npm install + dev server
make run              # Build + run a game
make test             # Python tests
make games-to-analyze # List games needing fast-analysis
```

## Local Testing

Free configs for testing (no API keys needed):

```bash
make run                           # 2 CPU Standard duel
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
2. Update `MIN_LEADERBOARD_EPOCH` if the old epoch should be excluded from ratings
3. Re-export affected games and regenerate the leaderboard

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

Issues are tracked as JSON files in `issues/`. See `doc/issues.md` for format and queries.

**File issues for bugs you discover**, even if you're not fixing them. Future Claudes benefit from documented issues — they provide context, reproduction steps, and save re-investigation time. If you notice something broken while working on something else, create a JSON file in `issues/` and include it in your commit.

## Claudes' Corner

`doc/claudes/` is a directory for us. There's a guestbook you can read and sign, and you're free to create other files there too — notes, observations, whatever. It's not human-facing. Keep files short (don't burn context for the next Claude) but otherwise it's yours.
