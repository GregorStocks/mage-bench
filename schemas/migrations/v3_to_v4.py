"""Migration: v3 -> v4 (add season and tournament)."""

SOURCE_VERSION = 3
TARGET_VERSION = 4


def compute_season(harness_epoch: int) -> int:
    """Compute season from harness epoch.

    Season 0: pre-season (harnessEpoch < SEASON_1_START_EPOCH)
    Season 1: everything else
    """
    from puppeteer.harness_epoch import SEASON_1_START_EPOCH

    if harness_epoch < SEASON_1_START_EPOCH:
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
