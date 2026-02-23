## Git

Local `master` is often behind. Always use `origin/master` as the source of truth.

**Never rebase, never force-push, never amend commits** — not even on feature branches. Always create new commits and merge:

```bash
git fetch origin
git merge origin/master
```

When this document or other instructions say "rebase", they mean "merge in master" as shown above.

## Code Isolation Philosophy

Avoid **modifying existing behavior** in Java outside of `Mage.Client.Streaming` and `Mage.Client.Headless`. This means not changing existing methods, fields, or logic in `Mage.Client`, `Mage.Server*`, `Mage.Common`, `Mage`, `Mage.Sets`, etc. Changing existing behavior makes incorporating upstream XMage updates difficult.

**Additive changes are OK:** Adding new methods, fields, or classes to upstream modules is fine as long as existing behavior is untouched — these merge cleanly.

**Bug fixes in upstream modules are OK** when we're confident they're XMage bugs (e.g. incorrect combat legality checks). File a P2 issue for tracking and keep the fix minimal.

**Our code (free to modify):**
- `Mage.Client.Streaming` - spectator client
- `Mage.Client.Headless` - bridge client
- `puppeteer/` - Python orchestration

## Architecture: MCP Layer vs Puppeteer

Game logic, Magic rules quirks, and XMage-specific workarounds belong in the **Java MCP layer** (`Mage.Client.Headless`), not in the puppeteer. The MCP layer should handle things like:

- Auto-tapping and mana payment fallbacks
- Filtering out unplayable actions (e.g. failed mana casts)
- Auto-passing priority when there are no meaningful choices
- Working around XMage UI quirks (modal dialogs, selection prompts)

The **puppeteer** (`puppeteer/`) should stay simple. Its job is to:

- Connect the MCP server to the LLMs via tool calls
- Provide additional tools for the LLMs (e.g. card lookup)
- Orchestrate the game lifecycle (start server, connect clients, record)

If you're tempted to add a special case or workaround in Python, consider whether it should live in Java instead. The LLMs should see a clean, high-level interface — the MCP layer absorbs the complexity.

## MCP Tools

When modifying MCP tool definitions or descriptions in `McpServer.java`, regenerate the tool definitions JSON used by the website:

```bash
make mcp-tools
```

This updates `website/src/data/mcp-tools.json`. Include the regenerated file in your commit.

## Persisted MCP Tool Results

MCP tool results are stored verbatim in `{player}_llm.jsonl` and `.json.gz` exports, which live forever. When changing MCP tool result formats (field names, structure), analysis code that reads persisted data must handle both old and new formats. The Python summarizer is the main consumer of choice-level fields — use `c.get("name", c.get("description", "?"))` patterns when reading fields that were renamed.

## Temporary Files

Use `tmp/` (in the repo root) as a scratch directory instead of `/tmp/`. It's gitignored and created by `worktree-setup.py`.

## Golden Tests

Golden test exports include `seq` numbers from the server that represent the actual game event sequence. **Never strip, normalize, or collapse seq numbers or snapshots** in golden export comparisons. If a golden test fails on CI with different seq numbers or missing/extra snapshots, the game is playing out differently — fix the root cause (usually nondeterministic auto-pass behavior in the bridge), don't mask it in the comparison.

## Testing

When changing Python code in `puppeteer/`, add or update tests in `puppeteer/tests/`. Run tests with:

```bash
make test
```

Tests run in CI alongside lint and typecheck. Keep tests fast and self-contained — use `tempfile` for file I/O, `unittest.mock.patch` for external dependencies.

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

## Pre-PR Checklist

Always run `make check` before creating a PR. This runs lint, typecheck, and tests in one shot:

```bash
make check
```

## Build System

**Always use `make` targets.** Never invoke `mvn`, `npm`, `npx`, or other build tools directly — the Makefile handles compilation, classpaths, and caching correctly. Running `mvn` directly causes stale class issues and skips necessary build steps.

```bash
make build          # Full Java build
make check          # Lint + typecheck + tests
make mcp-tools      # Compile + regenerate tool definitions
make website        # Leaderboard + npm install + dev server
make run            # Build + run a game
make test           # Python tests
```

If a `make` target doesn't exist for what you need, ask — don't improvise with raw `mvn`/`npm` commands.

## Python

Always use `uv` for Python. **Never** use `python3`, `pip`, `pip3`, or any system Python directly — not for running scripts, not for installing packages, not for anything. All Python execution must go through `uv`.

```bash
# Run a Python script
uv run python script.py

# Run a module
uv run --project puppeteer python -m puppeteer

# Install a package (NEVER use pip/pip3)
uv add some-package
```

## Running Games

Use `make run` with the `CONFIG` parameter:

```bash
# Default: 2 CPU Standard duel, no API keys needed
make run

# 4 random LLM pilots, random personalities and decks (needs OPENROUTER_API_KEY)
make run CONFIG=commander-gauntlet

# List all available configs
make configs

# Custom config file
make run CONFIG=path/to/my-config.json

# Record to specific file
make run OUTPUT=/path/to/video.mov

# Pass additional args
make run ARGS="--no-record"
```

Recordings are saved to `~/.mage-bench/logs/` by default.

## Local Testing

When running games for testing or verification, **only use free configs** that don't consume API tokens:

```bash
make run                              # No API keys needed (2 CPU Standard duel)
make run CONFIG=modern-staller      # No API keys needed (burn vs staller)
```

**Never run** `CONFIG=commander-gauntlet` or other LLM configs — these consume real API tokens and cost money.

## YouTube Uploads

YouTube API credentials are already set up at `~/.mage-bench/`. Don't check for their existence — just run the upload script and let it error out if something is wrong.

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

Exported `.json.gz` game files live in `website/public/games/`. These are the source of truth for blunder analysis, the leaderboard, and the game viewer. Raw game logs in `~/.mage-bench/logs/` are the pre-export format and don't have annotations or merged data.

## Logging

Game logs go to `~/.mage-bench/logs/game_YYYYMMDD_HHMMSS/`. See `doc/logging.md` for file layout and error logging architecture.

Symlinks for quick access (all relative, inside `~/.mage-bench/logs/`):
- `last-dumb`, `last-gauntlet`, etc. — most recent run per config name
- `last-branch-{name}` — most recent run on a given git branch (slashes replaced with dashes)

After running a game on your branch, check your branch symlink first:

```bash
ls -l ~/.mage-bench/logs/last-branch-GregorStocks-my-branch
```

## UI Terminology

When the user talks about "the UI", they mean the **Java Swing UI** (`StreamingGamePanel`) by default, not the website visualizer.

## Website

Use `make website` for all website development. This single command handles everything — generating leaderboard data, installing npm dependencies, and starting the Astro dev server:

```bash
make website
```

**Never** run `npm install`, `npx astro dev`, or other npm/npx commands directly. `make website` does it all.

Each worktree gets a unique port assigned by `worktree-setup.py` (written to `.env` as `WEBSITE_PORT`). Check your `.env` to find your port. Key pages (substitute your port):
- Home: `http://localhost:$WEBSITE_PORT/`
- Leaderboard: `http://localhost:$WEBSITE_PORT/leaderboard`
- Games list: `http://localhost:$WEBSITE_PORT/games`
- Live viewer (mock): `http://localhost:$WEBSITE_PORT/games/live?mock=1`
- Game replay: `http://localhost:$WEBSITE_PORT/games/{game_id}`

**Never use `pkill`, `killall`, or `lsof | kill` to stop dev servers.** Other Claudes are running their own dev servers on other ports in other worktrees. Killing by process name (`pkill -f astro`, `pkill -f node`) will take down everyone else's servers. To stop your dev server, kill the specific background task you started — don't carpet-bomb by process name.

## Screenshots

When working on UI changes, take screenshots to verify your work. See `doc/screenshots.md` for full details.

**Java Swing UI** (from game recordings):

```bash
make run                     # run a quick game (~2s)
make screenshot              # final frame -> <game_dir>/screenshot.png
make screenshot T=5          # frame at 5s into the game
# Then: Read the screenshot path from the output
```

**Website visualizer** (via Chrome browser automation):

Start the dev server with `make website`, then navigate Chrome to the pages listed above.

Use visual verification when:
- Modifying `StreamingGamePanel` layout or rendering
- Changing `website/public/game-renderer.js` or `game-renderer.css`
- Debugging card display or layout issues

## Issues

Issues are tracked as JSON files in `issues/`. See `doc/issues.md` for format and queries.

**File issues for bugs you discover**, even if you're not fixing them. Future Claudes benefit from documented issues — they provide context, reproduction steps, and save re-investigation time. If you notice something broken while working on something else, create a JSON file in `issues/` and include it in your commit.

## Claudes' Corner

`doc/claudes/` is a directory for us. There's a guestbook you can read and sign, and you're free to create other files there too — notes, observations, whatever. It's not human-facing. Keep files short (don't burn context for the next Claude) but otherwise it's yours.
