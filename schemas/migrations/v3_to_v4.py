"""Migration: v3 -> v4 (add season and tournament)."""

import sys
from pathlib import Path

SOURCE_VERSION = 3
TARGET_VERSION = 4

# Ensure puppeteer/src is importable for harness_epoch
_PUPPETEER_SRC = str(Path(__file__).resolve().parent.parent.parent / "puppeteer" / "src")
if _PUPPETEER_SRC not in sys.path:
    sys.path.insert(0, _PUPPETEER_SRC)


def compute_season(harness_epoch: int) -> int:
    """Compute season from harness epoch.

    Season 0: pre-season (harnessEpoch < MIN_LEADERBOARD_EPOCH)
    Season 1: everything else
    """
    from puppeteer.harness_epoch import MIN_LEADERBOARD_EPOCH

    if harness_epoch < MIN_LEADERBOARD_EPOCH:
        return 0
    return 1


def up(data: dict) -> dict:
    """Migrate from v3 to v4: add season and tournament."""
    assert data["version"] == 3, f"Expected v3, got v{data['version']}"

    data["season"] = compute_season(data["harnessEpoch"])
    data["tournament"] = None
    data["version"] = 4
    return data


def down(data: dict) -> dict:
    """Migrate from v4 to v3: remove season and tournament."""
    assert data["version"] == 4, f"Expected v4, got v{data['version']}"

    data.pop("season", None)
    data.pop("tournament", None)
    data["version"] = 3
    return data
