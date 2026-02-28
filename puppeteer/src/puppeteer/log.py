"""Centralized logging configuration for the puppeteer package.

Provides structured logging with levels (DEBUG, INFO, WARNING, ERROR)
and a consistent output format across all modules.
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

_setup_done = False


class _PuppeteerFormatter(logging.Formatter):
    """Custom formatter: timestamps always, level name only for WARNING+."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now().strftime("%H:%M:%S")
        msg = record.getMessage()
        if record.levelno >= logging.WARNING:
            return f"[{ts}] [{record.levelname}] {msg}"
        return f"[{ts}] {msg}"


def setup_logging(*, debug: bool = False) -> None:
    """Configure the root logger for puppeteer.

    Call once at process startup. Idempotent — subsequent calls are no-ops.

    Args:
        debug: If True, set level to DEBUG. Also checks env var
               PUPPETEER_LOG_LEVEL (DEBUG/INFO/WARNING/ERROR).
    """
    global _setup_done
    if _setup_done:
        return
    _setup_done = True

    env_level = os.environ.get("PUPPETEER_LOG_LEVEL", "").upper()
    if env_level in ("DEBUG", "INFO", "WARNING", "ERROR"):
        level = getattr(logging, env_level)
    elif debug:
        level = logging.DEBUG
    else:
        level = logging.INFO

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_PuppeteerFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger for the given module."""
    return logging.getLogger(name)


def log_error(
    logger: logging.Logger,
    game_dir: Path | None,
    username: str,
    msg: str,
) -> None:
    """Log an error and append to the per-player error file.

    Calls ``logger.error(msg)`` for console output, then appends to
    ``{username}_errors.log`` in game_dir (if provided).
    """
    logger.error(msg)
    if game_dir:
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            with open(game_dir / f"{username}_errors.log", "a") as f:
                f.write(f"[{ts}] {msg}\n")
        except OSError:
            pass
