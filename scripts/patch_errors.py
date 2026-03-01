#!/usr/bin/env python3
"""Patch existing game exports to fix broken error parsing and add decisionIndex.

Fixes errors that were exported with the old parser (source="unknown", ts="",
message containing embedded ISO timestamps) and links all errors to decisions.

Safe to run multiple times — already-fixed errors are left unchanged.
"""

import json
import re
from pathlib import Path

GAMES_DIR = Path(__file__).resolve().parent.parent / "website" / "public" / "games"

_ERROR_LINE_ISO_RE = re.compile(
    r"^\[\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})\.\d+[-+]\d{2}:\d{2}\]\s+\[(\w+)\]\s+(.+)$"
)


def _fix_error(err: dict) -> dict:
    """Re-parse a broken error entry (source=unknown with embedded ISO timestamp)."""
    if err.get("source") != "unknown" or err.get("ts") != "":
        return err
    m = _ERROR_LINE_ISO_RE.match(err["message"])
    if not m:
        return err
    return {
        "ts": m.group(1),
        "player": err["player"],
        "source": m.group(2),
        "message": m.group(3),
    }


def _link_errors_to_decisions(
    errors: list[dict], decisions: list[dict], llm_events: list[dict]
) -> None:
    """Add decisionIndex to each error by matching player + timestamp."""
    player_decisions: dict[str, list[tuple[str, int]]] = {}
    for d in decisions:
        player = d.get("player", "")
        indices = d.get("llmEventIndices", [])
        if not indices:
            continue
        source_event = llm_events[indices[0]]
        ts_iso = source_event.get("ts", "")
        if len(ts_iso) >= 19 and ts_iso[10] == "T":
            ts_hms = ts_iso[11:19]
        else:
            continue
        player_decisions.setdefault(player, []).append((ts_hms, d["index"]))

    for err in errors:
        err_ts = err.get("ts", "")
        if not err_ts:
            continue
        player = err.get("player", "")
        pd = player_decisions.get(player)
        if not pd:
            continue
        lo, hi = 0, len(pd)
        while lo < hi:
            mid = (lo + hi) // 2
            if pd[mid][0] <= err_ts:
                lo = mid + 1
            else:
                hi = mid
        if lo > 0:
            err["decisionIndex"] = pd[lo - 1][1]


def patch_game(path: Path) -> bool:
    """Patch a single game file. Returns True if modified."""
    data = json.loads(path.read_text())
    errors = data.get("errors")
    if not errors:
        return False

    changed = False

    # Fix broken error parsing
    for i, err in enumerate(errors):
        fixed = _fix_error(err)
        if fixed is not err:
            errors[i] = fixed
            changed = True

    # Add decisionIndex links
    decisions = data.get("decisions", [])
    llm_events = data.get("llmEvents", [])
    if decisions and llm_events:
        had_indices = any("decisionIndex" in e for e in errors)
        _link_errors_to_decisions(errors, decisions, llm_events)
        if not had_indices and any("decisionIndex" in e for e in errors):
            changed = True

    if changed:
        path.write_text(json.dumps(data, separators=(",", ":")))

    return changed


def main() -> None:
    patched = 0
    for path in sorted(GAMES_DIR.glob("game_*.json")):
        if patch_game(path):
            print(f"Patched {path.name}")
            patched += 1
    print(f"\n{patched} file(s) patched.")


if __name__ == "__main__":
    main()
