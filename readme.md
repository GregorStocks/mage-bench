# mage-bench

**[mage-bench.com](https://mage-bench.com/)**

Benchmark LLMs by having them play Magic: The Gathering against each other — 1v1 duels and multiplayer Commander.

Built on [XMage](https://github.com/magefree/mage), a full rules engine with enforcement for 28,000+ unique cards. LLMs interact via MCP tools exposed by the bridge — they see the board state, choose actions, and play full games with no manual intervention.

## Setup

### Prerequisites

- Java 17+ and Maven
- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- FFmpeg (for video recording)

### Download card images (optional)

Card images aren't included in the repo. Download them once via the XMage desktop client:

1. Run `make run-client` to launch the client.
2. Dismiss the "Unable connect to server" error — no server is needed for downloads.
3. Click **Download** in the top toolbar.
4. Download both **mana symbols** and **card images** separately. Pick a Scryfall source — "normal" is ~10 GB, "small" is ~1.5 GB.
5. Close the client when done. Images are cached in `plugins/images/` and reused by all future runs.

### Run a benchmark

```bash
export OPENROUTER_API_KEY="sk-..."
make run CONFIG=round-robin-commander
```

This runs 4 LLM pilots against each other in a Commander game with coverage-optimized matchmaking, observer mode, and video recording. Recordings and logs are saved to `~/.mage-bench/logs/`.

Other configs:

```bash
# Default: no API keys needed (2 CPU Standard duel)
make run

# 1 LLM pilot + 3 CPU opponents
make run CONFIG=commander-1v3

# Long-lived test server (stays running between games)
make run CONFIG=modern-staller

# List all available configs
make configs

# Custom config file
make run CONFIG=path/to/my-config.json

# Record to a specific file
make run OUTPUT=/path/to/video.mov
```

### YouTube upload (optional)

After a game finishes, the puppeteer prompts to upload the recording to YouTube.

1. Set up Google Cloud OAuth credentials (see `doc/youtube.md`).
2. Save the client secrets to `~/.mage-bench/youtube-client-secrets.json`.
3. To target a specific playlist, set `YOUTUBE_PLAYLIST_ID` in your environment or `.env` file. Defaults to the mage-bench playlist.

## Architecture

Three layers:

1. **XMage server** — upstream game engine, handles rules enforcement and game state. Unmodified from upstream.
2. **Java clients** (`Mage.Client.Bridge`, `Mage.Client.Observer`) — the bridge lets LLMs play via MCP tool calls, and the observer renders the game and records video.
3. **Puppeteer** (`puppeteer/`) — orchestrates everything: spawns processes, connects LLMs to bridge clients, tracks costs, manages recordings.

Game logic and XMage workarounds live in the Java bridge layer. The puppeteer stays simple.

## Player types

| Type | LLM? | Description |
|------|------|-------------|
| **Pilot** | Yes | Strategic LLM player — sees board state, chooses actions |
| **Sleepwalker** | No | MCP auto-player with chat, no LLM |
| **CPU** | No | XMage's built-in AI (COMPUTER_MAD) |
| **Potato** | No | Dumbest auto-player |
| **Staller** | No | Like potato but slow; stays connected between games |

Configure players in JSON config files (see `configs/`).

## Observer & recording

The spectator provides:
- Live game visualization (JavaFX)
- Video recording via FFmpeg

## Development

See `AGENTS.md` for development conventions, code isolation rules, and how to run things.

Based on [XMage](https://github.com/magefree/mage).
