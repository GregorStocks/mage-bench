"""Harness epoch tracking for game result comparability.

The harness epoch is a monotonic integer that marks breaking changes to the
evaluation harness (MCP tools, pilot logic, priority semantics). Games from
different epochs aren't directly comparable, so the leaderboard filters to
games at or above MIN_LEADERBOARD_EPOCH.
"""

# Current harness epoch. Bump when MCP tools, pilot logic, or priority
# semantics change enough to make game results non-comparable.
#
# History:
#   1 - Foundation: basic priority passing (Feb 10)
#   2 - yield_until + mana sourcing + error codes (Feb 12)
#   3 - Priority blocking + simplified pass_priority API (Feb 14)
#   4 - Short object IDs + batch combat (Feb 14)
#   5 - Fix parallel-game duplicate username disconnect loop (Feb 14)
#   6 - Bump max_tokens to 20k, fix GPT-5 Nano reasoning effort (Feb 14)
#   7 - Strip HTML tags and [xxx] hex ID suffixes from MCP tool results (Feb 15)
HARNESS_EPOCH = 7

# Minimum epoch for leaderboard inclusion. Games below this are shown
# in the games list but excluded from ELO ratings.
MIN_LEADERBOARD_EPOCH = 2

# Epoch boundary timestamps, used to infer epoch for games that predate
# epoch tracking. Format matches game_id timestamps: "YYYYMMDD_HHMMSS".
_EPOCH_BOUNDARIES = [
    ("20260212_224200", 2),  # yield_until landed
    ("20260214_084000", 3),  # priority blocking landed
    ("20260214_200000", 4),  # short object IDs + batch combat
    ("20260214_230000", 5),  # fix parallel-game duplicate username loop
    ("20260215_060000", 6),  # max_tokens 20k + GPT-5 Nano reasoning_effort=low
    ("20260215_100000", 7),  # strip HTML tags + [xxx] hex suffixes from MCP tool results
]


def infer_epoch(game_id: str, harness_epoch: int | None) -> int:
    """Return explicit epoch if present, else infer from game_id timestamp.

    For games recorded before epoch tracking was added, we infer the epoch
    from the game timestamp embedded in the game_id (game_YYYYMMDD_HHMMSS).
    """
    if harness_epoch is not None:
        return harness_epoch
    # Extract timestamp from game_id: "game_20260210_090609" -> "20260210_090609"
    ts = game_id.removeprefix("game_")
    epoch = 1
    for boundary_ts, boundary_epoch in _EPOCH_BOUNDARIES:
        if ts >= boundary_ts:
            epoch = boundary_epoch
    return epoch
