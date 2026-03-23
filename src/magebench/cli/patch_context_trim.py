#!/usr/bin/env python3
"""One-off patch: fix context_trim events in exported .json.gz files.

The pilot used to emit history_size/rendered_size but the export script
expected messages_before/messages_after, so all exported context_trim
events have messages_before=0, messages_after=0.  This script reads the
correct values from the raw llm logs and patches the exports in place.
"""

import gzip
import json
from pathlib import Path

GAMES_DIR = Path("website/public/games")
LOGS_DIR = Path.home() / ".mage-bench" / "logs"


def load_raw_trims(log_dir: Path) -> dict[tuple[str, str], dict]:
    """Load context_trim events from raw *_llm.jsonl files, keyed by (player, ts)."""
    trims: dict[tuple[str, str], dict] = {}
    for llm_file in log_dir.glob("*_llm.jsonl"):
        for line in llm_file.read_text().splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("type") == "context_trim":
                key = (event["player"], event["ts"])
                trims[key] = event
    return trims


def patch_game(game_path: Path) -> int:
    """Patch a single exported game file. Returns number of events fixed."""
    game_id = game_path.stem.removesuffix(".json")  # strip .json from .json.gz
    log_dir = LOGS_DIR / game_id

    if not log_dir.exists():
        return 0

    with gzip.open(game_path, "rt") as f:
        data = json.load(f)

    trim_events = [e for e in data["llmEvents"] if e.get("type") == "context_trim"]
    if not trim_events:
        return 0

    # Check if already patched (any non-zero value means it's good)
    if any(e.get("messagesBefore", 0) != 0 or e.get("messagesAfter", 0) != 0 for e in trim_events):
        return 0

    raw_trims = load_raw_trims(log_dir)
    if not raw_trims:
        return 0

    fixed = 0
    for event in trim_events:
        key = (event["player"], event["ts"])
        raw = raw_trims.get(key)
        if raw:
            event["messagesBefore"] = raw.get("history_size", 0)
            event["messagesAfter"] = raw.get("rendered_size", 0)
            fixed += 1

    if fixed:
        with gzip.open(game_path, "wt") as f:
            json.dump(data, f, separators=(",", ":"))

    return fixed


def main() -> None:
    game_files = sorted(GAMES_DIR.glob("*.json.gz"))
    total_fixed = 0
    games_patched = 0

    for game_path in game_files:
        fixed = patch_game(game_path)
        if fixed:
            games_patched += 1
            total_fixed += fixed
            print(f"  {game_path.name}: {fixed} events fixed")

    print(f"\nPatched {games_patched}/{len(game_files)} games, {total_fixed} events total")


if __name__ == "__main__":
    main()
