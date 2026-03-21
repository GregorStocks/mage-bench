#!/usr/bin/env python3
"""Backfill timedOut field on player entries in existing game exports.

Reads each .json/.json.gz in website/public/games/, scans actions for the
"has run out of time, losing the match." message, and sets timed_out=true on
matching players.

Skips games where any player already has timedOut set.

Usage:
    uv run python scripts/backfill_timed_out.py [--dry-run]
"""

import re
import sys
from pathlib import Path

from scripts.game_exports import (
    GAMES_DIR,
    glob_game_export_paths,
    load_raw_game_export,
    write_raw_game_export,
)

TIMED_OUT_RE = re.compile(r"^(.+?) has run out of time, losing the match\.$")


def backfill_game(path: Path, *, dry_run: bool = False) -> int:
    """Add timedOut to players in a single game export. Returns count of players patched."""
    data = load_raw_game_export(path)

    players = data["players"]
    if any(p.get("timedOut") is not None for p in players):
        return -1  # Already backfilled

    # Scan actions for timeout messages
    timed_out_names: set[str] = set()
    for a in data["actions"]:
        msg = a.get("message")
        m = TIMED_OUT_RE.match(msg) if msg else None
        if m:
            timed_out_names.add(m.group(1))

    if not timed_out_names:
        return 0

    # Patch players
    patched = 0
    for p in players:
        if p["name"] in timed_out_names:
            p["timedOut"] = True
            patched += 1

    if not dry_run and patched > 0:
        write_raw_game_export(path, data)

    return patched


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    unique_paths = glob_game_export_paths(GAMES_DIR)

    patched_games = 0
    skipped = 0
    no_timeout = 0
    total_players = 0
    for path in unique_paths:
        count = backfill_game(path, dry_run=dry_run)
        if count == -1:
            skipped += 1
            continue
        if count == 0:
            no_timeout += 1
            continue
        label = "(dry run)" if dry_run else ""
        print(f"{path.name}: {count} player(s) timed out {label}")
        patched_games += 1
        total_players += count

    print(
        f"\nDone: {patched_games} games patched ({total_players} players), "
        f"{no_timeout} games had no timeouts, {skipped} skipped (already backfilled)"
    )


if __name__ == "__main__":
    main()
