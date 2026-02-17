# Fast Game Analysis

Quickly analyze a game using only the exported `.json.gz` file. This covers ~85-90% of what the full analysis finds — game narrative, LLM decision quality, error patterns, bug identification — without needing the raw log directory.

## Workflow

### Step 1: Select the games

Determine which game(s) to analyze:

- If the user specified game ID(s), use those.
- If the user said "most recent" or similar, find the latest:
  ```bash
  uv run python scripts/list-recent-games.py
  ```
- If the user mentioned a config name (e.g. "commander-gauntlet", "standard-dumb", "modern-staller"), use the corresponding symlink:
  ```bash
  uv run python scripts/list-recent-games.py --config {config}
  ```
  where `{config}` might be `commander-gauntlet`, `commander-frontier`, `standard-dumb`, `modern-staller`, etc. Check what symlinks exist with `--symlinks`.
- **If no game specified at all**, find the 10 most recent unanalyzed games. Check which games already have fast-analysis files in `doc/claudes/analyses/fast/` and skip those:
  ```bash
  ls doc/claudes/analyses/fast/game_*.md 2>/dev/null  # already fast-analyzed
  ls website/public/games/*.json.gz | sort -r          # all games, newest first
  ```
  Pick the 10 most recent gz files that don't have a corresponding file in `fast/`. Analyze them one by one.

Run steps 2-5 for **each** selected game before moving to the next.

### Step 2: Find or generate the gz file

```bash
GAME_ID=game_YYYYMMDD_HHMMSS  # from step 1
GZ_PATH=website/public/games/${GAME_ID}.json.gz
```

a. Check if `$GZ_PATH` exists on the current branch.
b. If not, check if `~/.mage-bench/logs/${GAME_ID}/game_events.jsonl` exists. If so, generate the gz:
   ```bash
   uv run python scripts/export_game.py ${GAME_ID}
   ```
   This creates `website/public/games/${GAME_ID}.json.gz`.
c. If neither exists, tell the user and stop.

### Step 3: Use reusable analysis scripts

All analysis logic lives in `scripts/analysis/`. Check what already exists there before creating anything new — reuse or extend existing scripts. Run all scripts with `uv run python`.

If a script you need doesn't exist yet, **create it in `scripts/analysis/`** and check it in. Do NOT write inline `python3 -c "..."` one-liners. These scripts accumulate over time into a reusable analysis toolkit.

Each script should accept a gz file path as an argument:

```bash
uv run python scripts/analysis/game_overview.py $GZ_PATH
uv run python scripts/analysis/game_narrative.py $GZ_PATH
uv run python scripts/analysis/llm_events.py $GZ_PATH
uv run python scripts/analysis/llm_reasoning.py $GZ_PATH
```

The scripts should cover:

- **game_overview.py**: Game ID, format, turns, winner, player names/models/costs/placements.
- **game_narrative.py**: Turn-boundary board states (life, hand size, battlefield) and key actions (plays, casts, attacks, blocks, damage, etc.). Include chat messages prefixed with `[CHAT]`.
- **llm_events.py**: Event type counts by player, failed tool calls (with args and error messages), stalls/resets/auto-pilot/llm_error counts, and token/cost summaries.
- **llm_reasoning.py**: Sample 3-4 reasoning excerpts per player from `llm_response` events to assess decision quality (mulligan, combat, spell targeting).

**Smoking guns in reasoning and chat**: Pay close attention to what models complain about in their thinking traces and chat messages. When a model says things like "this doesn't make sense", "the tool returned wrong data", "I keep getting errors", or "why can't I cast this" — those are often smoking guns for platform bugs, not just model confusion. Cross-reference these complaints with the failed tool calls from `llm_events.py` to distinguish real bugs from model misunderstandings.

### Step 4: Check existing issues and file new ones

```bash
uv run python scripts/list-issues.py
```

Before filing a new issue, check whether the bug has already been fixed since the game was played. Compare the game date against recent commits:

```bash
git log --oneline --since="YYYY-MM-DD" origin/master  # date of the game
```

If a commit clearly fixes the bug, skip filing the issue. If unsure, file it and note the possibly-relevant commit in the description.

For each **code bug** found (not model behavior issues), create an issue in `issues/`:

```json
{
  "title": "Short summary",
  "description": "Description with evidence from gz analysis.\n\nEvidence:\n- game {game_id}: [error pattern description]\n- llmEvents tool_call failures: [count and pattern]\n\nSuggested fix: ...",
  "status": "open",
  "priority": N,
  "type": "task",
  "labels": ["relevant-labels"],
  "created_at": "YYYY-MM-DDTHH:MM:SS.000000-08:00",
  "updated_at": "YYYY-MM-DDTHH:MM:SS.000000-08:00"
}
```

Priority: P1 = crashes/broken actions, P2 = loops/stalling/repeated errors, P3 = bad tool descriptions/missing features, P4 = minor/cosmetic.

Labels: `bridge`, `puppeteer`, `pilot`, `spectator`

### Step 5: Log the analysis

Create a file in `doc/claudes/analyses/fast/` for each game analyzed (see `doc/claudes/analyses/README.md` for the template). This marks the game as fast-analyzed so future runs skip it.

### Step 6: Present summary

Summarize findings: game outcome, key plays, LLM quality assessment, bugs found (with issue filenames), and any model-only issues noted.

## What this skill does NOT do

- Read raw pilot logs, bridge logs, error logs, or server logs
- Trace bugs to specific source code lines
- Update `doc/investigating-game-logs.md`

For deeper analysis with source code tracing, use `/deep-analysis` instead.
