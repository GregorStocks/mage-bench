# Twitch + OBS Overlay Guide

The live game viewer runs in the website and polls the overlay API server
from `Mage.Client.Streaming`.

## Start streaming spectator

```bash
make run
```

Optional overlay controls:

```bash
# Custom overlay port
make run ARGS="--overlay-port 18080"

# Disable overlay server
make run ARGS="--no-overlay"
```

Run a long game (3 burn CPUs + 1 staller, 200 starting life, no time limit):

```bash
make run CONFIG=modern-staller
```

## URLs

The overlay API server defaults to `http://127.0.0.1:17888`. If that port is
already in use, it automatically moves to the next available port and prints
the chosen URL.

The overlay server serves the live viewer directly at `/live`. The URLs are
printed when the puppeteer starts:

- **Live viewer**: `http://127.0.0.1:17888/live`
- **OBS source** (positioned mode, transparent): `http://127.0.0.1:17888/live?positions=1&obs=1`
- **Mock data** (no running game needed): `http://127.0.0.1:17888/live?mock=1`

Query parameters:

| Param | Default | Description |
|-------|---------|-------------|
| `pollMs` | `700` | Polling interval in milliseconds |
| `positions` | off | `1` to enable pixel-positioned card hotspots (for OBS) |
| `obs` | off | `1` to hide nav/footer + transparent background |
| `mock` | off | `1` to use mock data |

## Staller personality

`Mage.Client.Headless` now supports a `staller` personality: it makes the same choices as `potato`, but responds slowly and stays connected between games.

Use it in game config:

```json
{
  "players": [
    {"type": "staller", "name": "Slowpoke", "deck": "random"}
  ]
}
```

Optional JVM override for staller delay:

```bash
-Dxmage.headless.stallerDelayMs=20000
```

## OBS setup

1. Keep the spectator running (`make run`).
2. In OBS, add your game/window capture source for XMage.
3. Add a Browser Source:
   - URL: `http://127.0.0.1:17888/live?positions=1&obs=1`
   - Width/Height: match your canvas (for example `1920x1080`)
   - Refresh browser when scene becomes active: enabled
5. If you want to test layout/hover interactions before a real game starts, add `&mock=1` to the URL.

## Overlay behavior

- The API server publishes live game state from the streaming spectator (`/api/state`).
- When `positions=1` is set, the live viewer uses exported card rectangles from the Swing UI and scales them to the browser source size.
- Cards are hoverable; hover opens a preview panel with card text and image.
- Card image URLs are built from Scryfall metadata in the game state.
- The live viewer also supports diff animations between polling updates (cards entering/leaving zones are highlighted).

## Twitch extension note

The live viewer with `obs=1` is designed to be Twitch-extension-friendly UI-wise and can be tested locally in OBS.

For a production Twitch extension (viewer-side interactive extension), you still need a hosted relay backend that distributes state to all viewers (viewer browsers cannot read the broadcaster's `localhost` overlay endpoint directly).
