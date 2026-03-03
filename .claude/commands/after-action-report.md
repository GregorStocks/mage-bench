# After-Action Report

Generate narrative after-action reports for games, saved as markdown files in the website's content collection and rendered at `/reports/{game_id}`.

## Workflow

### Step 1: Select the games

Determine which game(s) to report on:

- If the user specified game ID(s), use those.
- If the user said "most recent" or similar:
  ```bash
  uv run python scripts/list-recent-games.py
  ```
- If the user mentioned a config name:
  ```bash
  uv run python scripts/list-recent-games.py --config {config}
  ```
- **If no game specified at all**, find games that don't have reports yet:
  ```bash
  make games-to-report ARGS="--count 5"
  ```
  This cross-references all game exports in `website/public/games/` against existing reports in `website/src/content/reports/` and prints unreported games newest-first. Default is 5 games. Use `ARGS="--count N"` to change the number. Use `ARGS="--max-staleness 0"` to disable the staleness filter.
- **If `list-recent-games.py` fails** (e.g. logs directory missing in this worktree), fall back to listing exports directly:
  ```bash
  ls website/public/games/ | sort -r | head -5
  ```

**Check which games already have reports:**
```bash
for GAME_ID in $GAME_IDS; do
  ls website/src/content/reports/${GAME_ID}.md 2>/dev/null && echo "  ^ already exists"
done
```

Skip games that already have reports (unless the user explicitly asks to overwrite).

Run steps 2-5 for **each** selected game before moving to the next.

### Step 2: Resolve the game file path

```bash
GAME_ID=game_YYYYMMDD_HHMMSS  # from step 1
GAME_PATH=website/public/games/${GAME_ID}.json  # or .json.gz
```

a. Check if `website/public/games/${GAME_ID}.json` or `.json.gz` exists.
b. If not, check if `~/.mage-bench/logs/${GAME_ID}/game_events.jsonl` exists. If so, generate the export:
   ```bash
   uv run python scripts/export_game.py ${GAME_ID}
   ```
c. If neither exists, tell the user and skip to the next game.

### Step 3: Gather game data

Run the analysis scripts to collect raw material for the narrative:

```bash
uv run python scripts/analysis/game_overview.py $GAME_PATH
uv run python scripts/analysis/game_narrative.py $GAME_PATH
uv run python scripts/analysis/llm_reasoning.py $GAME_PATH
```

**Blunder annotations** — extract them directly from the game export. These are high-value narrative material:

```bash
uv run python -c "
from scripts.analysis.blunder_eval_common import load_game
d = load_game('$GAME_PATH')
for a in d.get('annotations', []):
    print(f'[{a[\"severity\"]}] {a[\"player\"]}: {a[\"description\"][:150]}')
"
```

**Snapshot indices per turn** — needed for linking to specific replay moments with `?s=N`:

```bash
uv run python -c "
from scripts.analysis.blunder_eval_common import load_game
d = load_game('$GAME_PATH')
seen = set()
for i, s in enumerate(d['snapshots']):
    t = s.get('turn', 0)
    if t not in seen:
        seen.add(t)
        print(f'Turn {t} starts at snapshot {i}')
"
```

**Note on `extract_decisions.py`**: The output can be extremely verbose (hundreds of lines) due to repeated mulligan decisions and forced choices. Only use it if the core scripts and annotations don't give you enough detail on specific decision points. When you do use it, pipe through `head -200`.

### Step 4: Write the report

Using the script outputs as context, write a narrative markdown report to:

```
website/src/content/reports/${GAME_ID}.md
```

#### Required frontmatter

```yaml
---
title: "A Compelling Title Summarizing the Game"
description: "One-sentence summary for the reports listing page."
gameId: "game_YYYYMMDD_HHMMSS"
pubDate: YYYY-MM-DDTHH:MM:SS
format: "Standard"  # or Modern, Legacy, Commander, Jumpstart
winner: "PlayerName"  # or null for draws
players:
  - name: "Player1"
    model: "provider/model-name"
    deck: "Deck Name"
  - name: "Player2"
    model: "provider/model-name"
    deck: "Deck Name"
totalTurns: 13
draft: false
---
```

- **title**: Creative, engaging — not just "Game Report". Reference the key drama.
- **description**: One sentence for the listing card. Spoil the outcome, that's fine.
- **gameId**: Must exactly match the game export filename (without extension).
- **pubDate**: Use today's date and a reasonable time.
- **format**: Extract from the game's `deckType` field. Map `Constructed - Standard` → `Standard`, `Constructed - Modern` → `Modern`, `Constructed - Legacy` → `Legacy`, `Variant Magic - Commander` / `Variant Magic - Freeform Commander` → `Commander`, `Limited` → `Jumpstart`.
- **players**: Extract from the game export. `deck` is the `deckName` or `commander` field.
- **winner**: The game's `winner` field. Use `null` (not quoted) for draws.

#### Body structure

Write an engaging narrative. Adapt sections to the game — short games don't need three phases. Suggested structure:

```markdown
## The Matchup

Brief intro: who's playing, what decks, format. Set the stage for the reader.

## Early Game

Opening plays, mulligan decisions, early development. What strategies emerge?

## Mid Game

Turning points, key combat, removal spells, blunders. This is usually where
the game is won or lost. Reference specific cards and plays.

## Late Game

How did the game close out? What sealed the deal? (Skip if game ended early.)

## Key Decisions

Highlight 2-3 pivotal decision points. If blunder annotations exist, reference
them. Link to specific replay snapshots:
[Turn 7 attack](/games/game_YYYYMMDD_HHMMSS?s=42)

## Verdict

Who played better and why. Brief model comparison. Any interesting patterns
in reasoning quality.
```

**Guidelines for narrative quality:**
- Write for someone who understands Magic but hasn't seen the game.
- Reference specific card names and plays — don't just say "played a creature."
- Include reasoning excerpts when they reveal interesting decision-making (quote them in blockquotes). Note: some models (e.g. MiniMax) don't expose reasoning — just skip reasoning excerpts for those players.
- Mention blunders naturally as part of the story, don't just list them.
- Link to replay snapshots for key moments using `?s=N` parameter (use the snapshot indices from step 3).
- Keep it concise — aim for 500-1000 words, not a novel.

### Step 5: Verify

Confirm the file was written and has valid frontmatter:

```bash
head -30 website/src/content/reports/${GAME_ID}.md
```

### Step 6: Present summary

After all games are processed, summarize what was done: how many reports were written, which games, and any games that were skipped (already had reports, missing exports, etc.).

## What this skill does NOT do

- File issues or log to `doc/claudes/analyses/`
- Assess LLM tool call statistics or cost efficiency in detail
- Replace fast-analysis or deep-analysis — those are developer-focused
