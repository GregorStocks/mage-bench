#!/usr/bin/env python3
"""Backfill timedOut field on player entries in existing game exports.

Reads each .json/.json.gz in website/public/games/, scans actions for the
"has run out of time, losing the match." message, and sets timedOut=true on
matching players.

Skips games where any player already has timedOut set.

Usage:
    uv run python scripts/backfill_timed_out.py [--dry-run]
"""

import gzip
import json
import re
import sys
from pathlib import Path

from scripts.export_game import _GZ_THRESHOLD

GAMES_DIR = Path(__file__).resolve().parent.parent / "website" / "public" / "games"
TIMED_OUT_RE = re.compile(r"^(.+?) has run out of time, losing the match\.$")


def backfill_game(path: Path, *, dry_run: bool = False) -> int:
    """Add timedOut to players in a single game export. Returns count of players patched."""
    if path.suffix == ".gz":
        raw = gzip.decompress(path.read_bytes())
        data = json.loads(raw)
    else:
        data = json.loads(path.read_text())

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
        json_str = json.dumps(data, indent=2, ensure_ascii=False)
        json_bytes = json_str.encode()

        if len(json_bytes) > _GZ_THRESHOLD:
            out_path = path.with_suffix("") if path.suffix == ".gz" else path
            out_path = out_path.with_suffix(".json.gz")
            out_path.write_bytes(gzip.compress(json_bytes))
            alt = (
                out_path.with_suffix(".json")
                if out_path.suffix == ".gz"
                else out_path.with_suffix(".json.gz")
            )
            if alt != path and alt.exists():
                alt.unlink()
            if path != out_path and path.exists():
                path.unlink()
        else:
            out_path = path.with_suffix("") if path.suffix == ".gz" else path
            out_path = out_path.with_suffix(".json")
            out_path.write_bytes(json_bytes)
            alt = out_path.with_suffix(".json.gz")
            if alt != path and alt.exists():
                alt.unlink()
            if path != out_path and path.exists():
                path.unlink()

    return patched


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    paths = sorted(GAMES_DIR.glob("game_*.json")) + sorted(
        GAMES_DIR.glob("game_*.json.gz")
    )
    # Deduplicate (a game might have both .json and .json.gz)
    seen: set[str] = set()
    unique_paths: list[Path] = []
    for p in paths:
        stem = p.name.replace(".json.gz", "").replace(".json", "")
        if stem not in seen:
            seen.add(stem)
            unique_paths.append(p)

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
