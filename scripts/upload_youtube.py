#!/usr/bin/env python3
"""Upload a game recording to YouTube."""

import importlib
import json
import os
import re
import sys
from functools import cache
from pathlib import Path
from typing import Any

MAGE_BENCH_DIR = Path.home() / ".mage-bench"
CLIENT_SECRETS_FILE = MAGE_BENCH_DIR / "youtube-client-secrets.json"
TOKEN_FILE = MAGE_BENCH_DIR / "youtube-token.json"
LOGS_DIR = Path.home() / ".mage-bench" / "logs"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

DEFAULT_PLAYLIST_ID = "PLZkLbT-AmvhB66wstXYqn4AmCYn4hmQ34"
PLAYLIST_ID = os.environ.get("YOUTUBE_PLAYLIST_ID", DEFAULT_PLAYLIST_ID)

DECKLIST_RE = re.compile(r"(?:SB:\s*)?(\d+)\s+\[([^:]+):([^\]]+)\]\s+(.+)")

_DECK_TYPE_TO_FORMAT: dict[str, str] = {
    "Constructed - Standard": "Standard",
    "Constructed - Modern": "Modern",
    "Constructed - Legacy": "Legacy",
    "Variant Magic - Freeform Commander": "Commander",
    "Variant Magic - Commander": "Commander",
}


class YouTubeUploadError(RuntimeError):
    """Operational upload failure that callers may treat as non-fatal."""


@cache
def _load_google_api_symbols() -> dict[str, Any]:
    """Load optional Google API symbols only when YouTube upload is used."""
    try:
        request_module = importlib.import_module("google.auth.transport.requests")
        credentials_module = importlib.import_module("google.oauth2.credentials")
        flow_module = importlib.import_module("google_auth_oauthlib.flow")
        discovery_module = importlib.import_module("googleapiclient.discovery")
        errors_module = importlib.import_module("googleapiclient.errors")
        http_module = importlib.import_module("googleapiclient.http")
    except ImportError as exc:
        raise ImportError(
            "YouTube upload requires google-api-python-client and google-auth-oauthlib.\nRun: cd puppeteer && uv sync"
        ) from exc

    return {
        "Request": request_module.Request,
        "Credentials": credentials_module.Credentials,
        "InstalledAppFlow": flow_module.InstalledAppFlow,
        "build": discovery_module.build,
        "HttpError": errors_module.HttpError,
        "MediaFileUpload": http_module.MediaFileUpload,
    }


def _format_label(meta: dict) -> str:
    """Derive a human-readable format label from game metadata."""
    deck_type = meta.get("deck_type")
    if not deck_type:
        return "Commander"
    return _DECK_TYPE_TO_FORMAT.get(deck_type, "Commander")


_COMMANDER_DECK_TYPES = {
    "Variant Magic - Freeform Commander",
    "Variant Magic - Commander",
}


def _extract_commander(player: dict) -> str | None:
    """Find commander name from decklist (SB: entries)."""
    decklist = player.get("decklist")
    if decklist is None:
        return None
    for entry in decklist:
        if entry.startswith("SB:"):
            m = DECKLIST_RE.match(entry)
            if m:
                return m.group(4).strip()
    return None


def _deck_name_from_path(deck_path: str) -> str | None:
    """Derive human-readable deck name from file path stem."""
    if not deck_path:
        return None
    return Path(deck_path).stem.replace("-", " ")


def _deck_display_name(player: dict, deck_type: str) -> str | None:
    """Get display name for a player's deck.

    For commander formats, returns the commander card name.
    For other formats, derives the name from the deck filename.
    """
    if deck_type in _COMMANDER_DECK_TYPES:
        return _extract_commander(player)
    deck_path = player.get("deck_path")
    if not deck_path:
        return None
    return _deck_name_from_path(deck_path)


def _build_title(meta: dict) -> str:
    """Generate video title from game metadata.

    Format: "mage-bench Format: Name (Deck) vs Name (Deck) vs ..."
    Truncated to 100 chars (YouTube limit).
    """
    deck_type = meta.get("deck_type")
    players = meta.get("players")
    parts = []
    if players is None:
        players = []
    for p in players:
        name = p.get("name", "?")
        deck_name = _deck_display_name(p, deck_type if deck_type else "")
        if deck_name:
            parts.append(f"{name} ({deck_name})")
        else:
            parts.append(name)

    matchup = " vs ".join(parts)
    fmt = _format_label(meta)
    title = f"mage-bench {fmt}: {matchup}"

    # Truncate to fit YouTube's 100-char limit
    if len(title) > 100:
        title = title[:97] + "..."
    return title


def _build_description(meta: dict, game_dir: Path) -> str:
    """Generate video description from game metadata."""
    game_id = game_dir.name
    game_url = f"https://mage-bench.com/games/{game_id}"

    deck_type = meta.get("deck_type")
    fmt = _format_label(meta)
    lines = [f"AI models play {fmt} (Magic: The Gathering) via mage-bench.", ""]

    players = meta.get("players")
    if players is None:
        players = []
    for p in players:
        deck_name = _deck_display_name(p, deck_type if deck_type else "")
        model = p.get("model")
        name = p.get("name", "?")
        parts = [name]
        if deck_name:
            parts.append(f"playing {deck_name}")
        if model:
            parts.append(f"({model})")
        lines.append(" ".join(parts))

    lines.append("")
    lines.append("Replay this game:")
    lines.append(game_url)
    lines.append("")
    lines.append("https://mage-bench.com")

    return "\n".join(lines)


def _get_authenticated_service() -> Any:
    """Build an authenticated YouTube API service."""
    google_api = _load_google_api_symbols()
    request_cls = google_api["Request"]
    credentials_cls = google_api["Credentials"]
    installed_app_flow_cls = google_api["InstalledAppFlow"]
    build = google_api["build"]

    creds = None

    if TOKEN_FILE.exists():
        creds = credentials_cls.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(request_cls())
        else:
            if not CLIENT_SECRETS_FILE.exists():
                raise FileNotFoundError(
                    f"YouTube client secrets not found at {CLIENT_SECRETS_FILE}.\n"
                    "See doc/youtube.md for setup instructions."
                )
            flow = installed_app_flow_cls.from_client_secrets_file(
                str(CLIENT_SECRETS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)

        MAGE_BENCH_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def upload_to_youtube(game_dir: Path) -> str | None:
    """Upload recording.mov from game_dir to YouTube.

    Returns the YouTube video URL on success, None if no recording exists.
    """
    try:
        google_api = _load_google_api_symbols()
        http_error_cls = google_api["HttpError"]
        media_file_upload_cls = google_api["MediaFileUpload"]
    except ImportError as exc:
        raise YouTubeUploadError(str(exc)) from exc

    recording = game_dir / "recording.mov"
    if not recording.exists():
        print(f"  No recording.mov in {game_dir}, skipping YouTube upload")
        return None

    try:
        meta_path = game_dir / "game_meta.json"
        meta = {}
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())

        title = _build_title(meta)
        description = _build_description(meta, game_dir)

        print(f"  Uploading to YouTube: {title}")

        youtube = _get_authenticated_service()

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": [
                    "mage-bench",
                    "magic-the-gathering",
                    "xmage",
                    "ai",
                    "llm",
                    "commander",
                ],
                "categoryId": "20",  # Gaming
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        media = media_file_upload_cls(
            str(recording),
            mimetype="video/quicktime",
            resumable=True,
            chunksize=10 * 1024 * 1024,
        )

        request = youtube.videos().insert(
            part="snippet,status", body=body, media_body=media
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                print(f"\r  Upload progress: {pct}%", end="", flush=True)

        video_id = response["id"]
        url = f"https://youtu.be/{video_id}"
        print(f"\r  Upload complete: {url}  ")

        # Add to playlist
        try:
            youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": PLAYLIST_ID,
                        "resourceId": {
                            "kind": "youtube#video",
                            "videoId": video_id,
                        },
                    },
                },
            ).execute()
            print("  Added to playlist")
        except http_error_cls as e:
            print(f"  Warning: failed to add to playlist: {e}")
    except (
        ImportError,
        FileNotFoundError,
        OSError,
        json.JSONDecodeError,
        http_error_cls,
    ) as exc:
        raise YouTubeUploadError(str(exc)) from exc

    return url


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <game_id>")
        print(f"  game_id: directory name under {LOGS_DIR}")
        sys.exit(1)

    game_id = sys.argv[1]
    game_dir = LOGS_DIR / game_id
    if not game_dir.is_dir():
        print(f"Error: {game_dir} is not a directory")
        sys.exit(1)

    url = upload_to_youtube(game_dir)
    if url:
        # Save to game_meta.json
        meta_path = game_dir / "game_meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            meta["youtube_url"] = url
            meta_path.write_text(json.dumps(meta, indent=2) + "\n")
            print(f"  Saved YouTube URL to {meta_path}")


if __name__ == "__main__":
    main()
