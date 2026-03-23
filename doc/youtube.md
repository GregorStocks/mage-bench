# YouTube Upload

Game recordings can be uploaded to YouTube after each game.

## Setup

### 1. Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or use an existing one)
3. Enable the **YouTube Data API v3**
4. Go to **APIs & Services > Credentials**
5. Create an **OAuth 2.0 Client ID** (application type: **Desktop**)
6. Download the JSON credentials file

### 2. Place Credentials

Save the downloaded JSON file to:

```text
~/.mage-bench/youtube-client-secrets.json
```

### 3. First Use

The first time you upload, a browser window will open for Google OAuth consent. After granting access, a refresh token is saved to `~/.mage-bench/youtube-token.json` so future uploads don't need browser auth.

## Usage

### After a game

When a game finishes with a recording, the puppeteer prompts:

```text
Upload recording to YouTube? [y/N]:
```

### Standalone

```bash
make upload-youtube GAME=game_20260210_074307
```

Or directly:

```bash
uv run python -m magebench.cli.upload_youtube game_20260210_074307
```

## Details

- Videos are uploaded as **unlisted** (accessible via URL, not searchable)
- Category: Gaming
- The YouTube URL is saved to `game_meta.json` and propagated to the website JSON on export
- YouTube dependencies are installed automatically with `uv sync`
