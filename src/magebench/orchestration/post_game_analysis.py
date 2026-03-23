"""Post-game export, upload, and annotation helpers for orchestrator runs."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAIError

from magebench.analysis.blunder.blunder_analysis import BlunderAnalysisError
from magebench.analysis.blunder.blunder_analysis import main as _analyze_blunders
from magebench.common.log import get_logger
from magebench.common.youtube_upload import YouTubeUploadError
from magebench.common.youtube_upload import upload_to_youtube as _upload_to_youtube
from magebench.game.export_game import GameExportError
from magebench.game.export_game import export_game as _export_game
from magebench.game.game_exports import load_raw_game_export, write_raw_game_export

logger = get_logger(__name__)


def save_youtube_url(game_dir: Path, url: str) -> None:
    """Save a YouTube URL to game_meta.json if it exists."""
    meta_path = game_dir / "game_meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        meta["youtube_url"] = url
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")


def update_website_youtube_url(game_dir: Path, url: str, project_root: Path) -> None:
    """Patch a YouTube URL into exported website data if it already exists."""
    game_id = game_dir.name
    website_games_dir = project_root / "website" / "public" / "games"

    for game_path in (
        website_games_dir / f"{game_id}.json5",
        website_games_dir / f"{game_id}.json5.gz",
    ):
        if not game_path.exists():
            continue
        data = load_raw_game_export(game_path)
        data["youtube_url"] = url
        write_raw_game_export(game_path, data, compress=game_path.suffix == ".gz")
        break

    index_json = website_games_dir / "index.json"
    if index_json.exists():
        index = json.loads(index_json.read_text())
        for entry in index:
            if entry.get("id") == game_id:
                entry["youtube_url"] = url
                break
        index_json.write_text(json.dumps(index, indent=2))


@dataclass
class AnnotationFailure:
    """A game that was exported but failed annotation, pending user decision."""

    final_path: Path
    error: str
    game_id: str


def _attempt_annotation(gz_path: Path, max_retries: int = 2) -> tuple[str | None, float]:
    """Try to annotate a game file, with automatic retries."""
    last_error = ""
    for attempt in range(1 + max_retries):
        try:
            cost = _analyze_blunders(str(gz_path))
            return None, cost
        except (BlunderAnalysisError, OpenAIError) as exc:
            last_error = str(exc)
            logger.warning("  Annotation attempt %d failed: %s", attempt + 1, exc)
            if attempt < max_retries:
                logger.warning("  Retrying (%d/%d)...", attempt + 2, 1 + max_retries)
    return last_error, 0.0


def _prompt_annotation_failure(game_id: str, error: str) -> str:
    """Ask the user what to do about a failed annotation."""
    logger.warning("  Annotation failed for %s: %s", game_id, error)
    while True:
        try:
            answer = input("  [r]etry / [e]mit without annotation / [s]kip? ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            logger.info("")
            return "skip"
        if answer in ("r", "retry"):
            return "retry"
        if answer in ("e", "emit"):
            return "emit"
        if answer in ("s", "skip"):
            return "skip"
        logger.info("  Unrecognized answer: %r", answer)


def _finalize_export(tmp_path: Path, final_path: Path) -> None:
    """Move an exported game from its temp location to the final website path."""
    shutil.move(str(tmp_path), str(final_path))
    size_kb = final_path.stat().st_size // 1024
    logger.info("  Exported for website: %s (%d KB)", final_path, size_kb)


def resolve_annotation_failures(failures: list[AnnotationFailure]) -> None:
    """Prompt the user about each deferred annotation failure."""
    if not failures:
        return
    logger.info("  %d game(s) failed annotation:", len(failures))
    for failure in failures:
        while True:
            action = _prompt_annotation_failure(failure.game_id, failure.error)
            if action == "retry":
                err, _cost = _attempt_annotation(failure.final_path, max_retries=0)
                if err is None:
                    break
                failure.error = err
                continue
            if action == "emit":
                break
            failure.final_path.unlink(missing_ok=True)
            logger.info("  Skipped %s", failure.game_id)
            break


def upload_and_export(
    game_dir: Path,
    project_root: Path,
    *,
    deferred_failures: list[AnnotationFailure] | None = None,
    post_game_failures: list[str] | None = None,
) -> float:
    """Upload recording to YouTube and export a game for the website."""
    recording = game_dir / "recording.mov"
    game_id = game_dir.name

    if recording.exists():
        try:
            url = _upload_to_youtube(game_dir)
            if url:
                logger.info("  YouTube: %s", url)
                save_youtube_url(game_dir, url)
                update_website_youtube_url(game_dir, url, project_root)
        except (YouTubeUploadError, OSError, json.JSONDecodeError) as exc:
            logger.warning("  YouTube upload failed: %s", exc)
            if post_game_failures is not None:
                post_game_failures.append(f"{game_id}: YouTube upload failed: {exc}")

    website_games_dir = project_root / "website" / "public" / "games"
    try:
        website_games_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=website_games_dir) as tmp_dir:
            tmp_export_path = _export_game(game_dir, Path(tmp_dir))
            final_path = website_games_dir / tmp_export_path.name
            tmp_path = website_games_dir / f".tmp_{tmp_export_path.name}"
            shutil.move(str(tmp_export_path), str(tmp_path))
    except (GameExportError, OSError) as exc:
        logger.warning("  Website export failed: %s", exc)
        if post_game_failures is not None:
            post_game_failures.append(f"{game_id}: Website export failed: {exc}")
        return 0.0

    _finalize_export(tmp_path, final_path)

    if not os.environ.get("OPENROUTER_API_KEY"):
        return 0.0

    err, cost = _attempt_annotation(final_path)
    if err is None:
        return cost

    if deferred_failures is not None:
        deferred_failures.append(AnnotationFailure(final_path, err, game_id))
        logger.info("  Deferred annotation failure for %s (will ask at end)", game_id)
        return 0.0

    while True:
        action = _prompt_annotation_failure(game_id, err)
        if action == "retry":
            err, cost = _attempt_annotation(final_path, max_retries=0)
            if err is None:
                return cost
            continue
        if action == "emit":
            if post_game_failures is not None:
                post_game_failures.append(f"{game_id}: Blunder analysis failed: {err}")
            return 0.0
        if post_game_failures is not None:
            post_game_failures.append(f"{game_id}: Blunder analysis failed (skipped): {err}")
        final_path.unlink(missing_ok=True)
        logger.info("  Skipped %s", game_id)
        return 0.0
