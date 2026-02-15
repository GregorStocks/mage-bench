# Blunder Annotator

Find a game that hasn't been blunder-analyzed yet and run the analysis script on it.

## Step 1: Find an un-annotated game

```bash
uv run python -c "
import gzip, json, glob
for gz in sorted(glob.glob('website/public/games/game_*.json.gz')):
    with gzip.open(gz, 'rt') as f:
        data = json.load(f)
    if 'annotations' not in data:
        print(gz)
        break
"
```

If the user specified a particular game, use that instead regardless of annotation status.

If all games are already annotated, tell the user.

## Step 2: Run the analysis

```bash
uv run --project puppeteer python scripts/analysis/blunder_analysis.py <game.json.gz>
```

The script handles everything automatically:
- Extracts decisions from the game export
- Pre-filters with Claude Haiku to find suspicious decisions (cheap)
- Analyzes flagged decisions with Claude Opus 4.6 for detailed blunder annotations
- Validates and writes annotations back to the .json.gz file
- Prints a cost breakdown at the end

Requires `OPENROUTER_API_KEY` environment variable.
