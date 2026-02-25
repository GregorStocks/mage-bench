# Screenshots

Two paths for visually inspecting the UI: extracting frames from game recordings (Java Swing UI) and browsing the website visualizer (Chrome automation).

## Java Swing UI

Every game run with `--observer --record` (the default for `make run-dumb`, `make run-llm`, etc.) saves a video recording to `~/.mage-bench/logs/game_YYYYMMDD_HHMMSS/recording.mov`. Extract a frame with:

```bash
make screenshot
```

This saves `screenshot.png` inside the game's log directory from the most recent game recording (~0.5s before the end). Then view it:

```
Read ~/.mage-bench/logs/game_.../screenshot.png
```

### Parameters

| Param | Default | Description |
|-------|---------|-------------|
| `T` | `-0.5` | Time position. Negative = seconds before end, positive = seconds from start |
| `GAME` | most recent | Path to game log directory |
| `FILE` | `<game_dir>/screenshot.png` | Output file path |

### Examples

```bash
# Final game state (default)
make screenshot

# 5 seconds into the game
make screenshot T=5

# 2 seconds before the end
make screenshot T=-2

# From a specific game
make screenshot GAME=~/.mage-bench/logs/game_20260208_220934

# Custom output path
make screenshot FILE=./my-screenshot.png

# Manual ffmpeg (equivalent to default)
ffmpeg -y -sseof -0.5 -i ~/.mage-bench/logs/game_.../recording.mov -frames:v 1 screenshot.png
```

## Website Visualizer

The website has a game renderer that can display replays and live games. Use Chrome browser automation (MCP tools) to navigate and screenshot.

### Prerequisites

Start the dev server:

```bash
make website
```

### Mock data (no running game needed)

Navigate Chrome to:

```
http://localhost:4321/games/live?mock=1
```

This loads built-in mock game state — useful for testing CSS/JS changes to the renderer without running a real game.

### Game replay

After running a game, export it for the website:

```bash
make export-game GAME=game_20260208_220934
```

Then navigate Chrome to:

```
http://localhost:4321/games/game_20260208_220934
```

### Chrome automation steps

1. `tabs_context_mcp` — get current browser context
2. `tabs_create_mcp` — create a new tab
3. `navigate` to the URL
4. `computer screenshot` — capture the page

## When to Use Which

**Swing recording screenshot** — shows the actual Java UI that gets recorded to video. Use when:
- Verifying Java UI rendering, card images, or layout changes
- Checking what the recording looks like
- Debugging `ObserverGamePanel` issues

**Website visualizer** — shows the web-based game renderer. Use when:
- Verifying CSS/JS changes to `game-renderer.js` or `game-renderer.css`
- Testing with mock data (no game needed)
- Interactive debugging with hover/click
- Checking the replay UI (transport controls, life graph, etc.)
