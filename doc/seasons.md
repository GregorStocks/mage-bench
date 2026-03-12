# Season Lifecycle

A season is a cycle of regular-season games followed by a postseason tournament. The current state lives in `data/season.json`:

```json
{
  "current_season": 1,
  "phase": "regular-season"
}
```

Two phases: `"regular-season"` (normal games allowed) and `"tournament"` (regular-season games blocked, only tournament matches run).

## Full Flow

### 1. Regular Season

During the regular season, run games normally:

```bash
make run                               # CPU duel (no API keys)
make run CONFIG=round-robin-commander   # LLM pilots
```

Each game's `game_meta.json` is tagged with the current season number. Games with `season >= 1` are rated and contribute to the leaderboard.

Export finished games for the website:

```bash
make export-game GAME=game_20260306_170455
```

### 2. Conclude the Season (enter tournament phase)

When enough games have been played, conclude the season to lock in the top players and create a tournament bracket:

```bash
make conclude-season            # top 8 (default)
make conclude-season SIZE=16    # top 16
```

This runs `make leaderboard` first (as a dependency), then:

1. Reads the combined leaderboard (`website/src/data/benchmark-results.json`)
2. Selects the top N players by Elo rating
3. Randomly assigns unique personalities to each entrant
4. Creates `data/tournaments/season-N.json` with entrants, seedings, and an empty bracket
5. Updates `data/season.json` to `"phase": "tournament"`

After this, `make run` is **blocked** — the orchestrator rejects non-tournament games during tournament phase.

Script: `scripts/conclude_season.py`

### 3. Tournament Draft

Each entrant's LLM picks two Jumpstart half-deck packs via a snake draft:

```bash
make tournament-draft
```

1. Generates snake draft order: `[1, 2, ..., N, N, ..., 2, 1]` (two rounds)
2. For each pick, presents 4 random pack options with card details (fetched from Scryfall)
3. Calls the entrant's LLM to choose a pack (with reasoning)
4. Combines each entrant's 2 picked half-decks into their tournament decklist
5. Saves the `draft` object (picks, reasoning, decklists) to the tournament JSON

Script: `scripts/tournament_draft.py`

### 4. Run Tournament Matches

Play single-elimination bracket matches:

```bash
make tournament-game             # play the next match
make tournament-game GAMES=3     # play the next 3 matches
```

Each invocation:

1. Finds the next unplayed match in the bracket (generates rounds on demand using seeded fold pairing: `(1,8), (4,5), (2,7), (3,6)` for 8 players)
2. Writes tournament decklists to `tmp/tournament-decks/`
3. Builds a game config with `tournamentGame: true` (bypasses the regular-season block)
4. Runs the game via the orchestrator
5. Extracts the winner from `server_game_events.jsonl`
6. Records `winner_seed` and `game_id` in the tournament JSON
7. When the bracket is complete, prints the champion

Round naming: Finals, Semifinals, Quarterfinals, Round of N.

Script: `scripts/tournament_game.py`

### 5. Conclude the Tournament / Start Next Season

**This step does not exist yet.** See `issues/conclude-tournament-crown-winner.json`.

The planned `make conclude-tournament` target would:

1. Verify all rounds are complete (bracket has a final winner)
2. Record the season champion in the tournament JSON
3. Bump `current_season` in `data/season.json` to N+1
4. Reset `phase` to `"regular-season"` and clear the `tournament` pointer
5. Print a summary of the tournament results

This unblocks `make run` for the next season's regular-season games. The completed tournament file stays in `data/tournaments/` as a historical record.

Until this is implemented, transitioning to the next season requires manually editing `data/season.json`.

### 6. Regular Season (Next Season)

Once the season is back in `"regular-season"` phase (with `current_season` incremented), `make run` works again. New games are tagged with the new season number. The cycle repeats from step 1.

## Key Files

| Path | Purpose |
| ------ | --------- |
| `data/season.json` | Current season number and phase |
| `data/tournaments/season-N.json` | Tournament bracket, entrants, draft, results |
| `website/src/data/benchmark-results.json` | Combined leaderboard (all seasons) |
| `website/src/data/benchmark-results-season-N.json` | Per-season leaderboard |
| `puppeteer/presets.json` | Model configurations |
| `puppeteer/personalities.json` | LLM personality definitions |
| `data/decks/jumpstart/` | Jumpstart half-deck packs for drafting |
| `scripts/conclude_season.py` | Conclude season script |
| `scripts/tournament_draft.py` | Snake draft script |
| `scripts/tournament_game.py` | Tournament match runner |

## Enforcement

- **Regular-season block**: `orchestrator.py:_check_season_tournament_block()` rejects `make run` during tournament phase. Tournament games (with `tournamentGame: true`) and test configs (with `skip_post_game_prompts: true`) are exempt.
- **Leaderboard**: Games with `season == 0` are unrated. Only `season >= 1` games contribute to Elo.
- **Website**: `scripts/generate_leaderboard.py` copies `data/season.json` to `website/src/data/season.json` at build time. The season page at `/season/N` renders the tournament bracket.

## Command Summary

```bash
# --- Regular season ---
make run [CONFIG=...]              # Run a game (blocked during tournaments)
make export-game GAME=...         # Export game for website
make leaderboard                  # Regenerate Elo ratings

# --- Season conclusion ---
make conclude-season [SIZE=8|16]  # Lock top players, create bracket, enter tournament phase

# --- Tournament ---
make tournament-draft             # Snake draft: each LLM picks 2 Jumpstart packs
make tournament-game [GAMES=N]    # Play next N bracket matches

# --- Next season (not yet implemented) ---
# make conclude-tournament        # Crown winner, bump season, return to regular-season
```
