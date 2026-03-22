"""Shared helpers for leaderboard generation modules."""

from __future__ import annotations

import gzip
import re
from pathlib import Path

from magebench.game.game_export_types import GameExport, parse_game_export

_GENERATED_AT_RE = re.compile(r'"generatedAt":\s*"[^"]*",?\n?')


def write_if_changed(path: Path, content: str) -> bool:
    """Write content to path only if something besides generatedAt changed."""
    try:
        existing = path.read_text()
    except FileNotFoundError:
        path.write_text(content)
        return True
    if existing == content:
        return False
    if _GENERATED_AT_RE.sub("", existing) == _GENERATED_AT_RE.sub("", content):
        return False
    path.write_text(content)
    return True


def load_game_file(path: Path) -> GameExport:
    """Load a game export file (.json5 or .json5.gz)."""
    raw = gzip.decompress(path.read_bytes()).decode() if path.suffix == ".gz" else path.read_text()
    return parse_game_export(raw, source=path.name)


def glob_game_files(games_dir: Path) -> list[Path]:
    """Find all game export files (.json5 and .json5.gz) in a directory, sorted."""
    gz_files = set(games_dir.glob("game_*.json5.gz"))
    gz_stems = {path.name.removesuffix(".gz") for path in gz_files}
    json5_files = [path for path in games_dir.glob("game_*.json5") if path.name not in gz_stems]
    return sorted(gz_files | set(json5_files))
