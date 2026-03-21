"""Shared helpers for repo convention tests."""

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PUPPETEER_DIR = REPO_ROOT / "puppeteer"
GAMES_DIR = REPO_ROOT / "website" / "public" / "games"
DECKS_DIR = REPO_ROOT / "data" / "decks"
CONFIGS_DIR = REPO_ROOT / "configs"

# Special preset/personality keywords resolved at runtime, not looked up in JSON.
SPECIAL_PRESET_KEYWORDS = {"random", "round-robin"}
SPECIAL_PERSONALITY_KEYWORDS = {"random"}

# Models that were retired from models.json but still appear in historical
# exported games. Add entries here when removing a model.
RETIRED_MODELS: set[str] = {
    "mistralai/devstral-small",
}

# The canonical set of deck format directories under data/decks/.
EXPECTED_DECK_FORMATS = {"standard", "modern", "legacy", "commander", "jumpstart"}


def load_json(path: Path) -> object:
    return json.loads(path.read_text())


def all_game_files() -> list[Path]:
    gz_files = set(GAMES_DIR.glob("game_*.json5.gz"))
    gz_stems = {p.name.removesuffix(".gz") for p in gz_files}
    json_files = [p for p in GAMES_DIR.glob("game_*.json5") if p.name not in gz_stems]
    return sorted(gz_files | set(json_files))


def changed_files_since_master() -> set[str] | None:
    """Return repo-relative paths changed since master, or None if on master / git fails."""
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if branch == "master":
            return None

        merge_base = subprocess.run(
            ["git", "merge-base", "master", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        diff_result = subprocess.run(
            ["git", "diff", "--name-only", merge_base],
            capture_output=True,
            text=True,
            check=True,
        )
        untracked_result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            check=True,
        )

        changed = set(diff_result.stdout.strip().splitlines())
        changed.update(untracked_result.stdout.strip().splitlines())
        return changed
    except subprocess.CalledProcessError:
        return None


def changed_game_filenames() -> set[str] | None:
    """Return filenames of game exports changed since master, or None for all.

    Returns None (= validate everything) when on master, when the export
    script or schema changed, or when git commands fail.
    """
    changed = changed_files_since_master()
    if changed is None:
        return None

    schema_files = {f for f in changed if f.startswith("schemas/game-export-v") and f.endswith(".schema.json")}
    if schema_files or "scripts/export_game.py" in changed:
        return None

    prefix = "website/public/games/"
    return {f.removeprefix(prefix) for f in changed if f.startswith(prefix)}


def glob_game_files() -> list[Path]:
    """Game export files to validate.

    By default only files changed since master are returned.
    Set CHECK_ALL_EXPORTS=1 to validate every export.
    """
    all_files = all_game_files()

    if os.environ.get("CHECK_ALL_EXPORTS") == "1":
        return all_files

    changed = changed_game_filenames()
    if changed is None:
        return all_files

    return [f for f in all_files if f.name in changed]
