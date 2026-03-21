"""Error parsing helpers for game export construction."""

import re
from pathlib import Path

from schemas.game_export_types import Decision

_ERROR_LINE_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s+\[(\w+)\]\s+(.+)$")
_ERROR_LINE_ISO_RE = re.compile(
    r"^\[\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})\.\d+[-+]\d{2}:\d{2}\]\s+\[(\w+)\]\s+(.+)$"
)

_LLM_ERROR_PREFIXES = (
    "choose_action failed:",
    "Loop detected",
    "MCP request failed",
    "OUTPUT TRUNCATED:",
    "Action failed:",
    "Empty response from LLM",
    "LLM appears degraded",
    "Stalled:",
    "LLM request timed out",
    "LLM error:",
    "Maximum game duration exceeded",
    "Auto-pass",
    "Too many consecutive errors",
    "Auto-pass loop reached max iterations",
)


def _is_llm_error(message: str) -> bool:
    """Return True if this error message is an LLM mistake, not a code bug."""
    return any(message.startswith(prefix) for prefix in _LLM_ERROR_PREFIXES)


def read_errors(game_dir: Path) -> list[dict]:
    """Read per-player error logs and return structured error entries.

    Each entry has: ts (HH:MM:SS or ""), player, source (pilot/mcp/unknown),
    message. LLM-caused errors are filtered out, so only infrastructure bugs
    surface as critical errors.
    """
    errors: list[dict] = []
    for log_file in sorted(game_dir.glob("*_errors.log")):
        player = log_file.stem.removesuffix("_errors")
        try:
            for line in log_file.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                m = _ERROR_LINE_RE.match(line) or _ERROR_LINE_ISO_RE.match(line)
                if m:
                    message = m.group(3)
                    if _is_llm_error(message):
                        continue
                    errors.append(
                        {
                            "ts": m.group(1),
                            "player": player,
                            "source": m.group(2),
                            "message": message,
                        }
                    )
                else:
                    errors.append(
                        {
                            "ts": "",
                            "player": player,
                            "source": "unknown",
                            "message": line,
                        }
                    )
        except OSError:
            pass
    return errors


def link_errors_to_decisions(
    errors: list[dict], decisions: list[Decision], llm_events: list[dict]
) -> None:
    """Add decisionIndex to each error by matching player + timestamp.

    For each error, finds the most recent decision for the same player whose
    source event timestamp is <= the error timestamp. Modifies errors in place.
    """
    player_decisions: dict[str, list[tuple[str, int]]] = {}
    for decision in decisions:
        indices = decision.llm_event_indices
        if not indices:
            continue
        source_event = llm_events[indices[0]]
        ts_iso = source_event.get("ts")
        if ts_iso and len(ts_iso) >= 19 and ts_iso[10] == "T":
            ts_hms = ts_iso[11:19]
        else:
            continue
        player_decisions.setdefault(decision.player, []).append(
            (ts_hms, decision.index)
        )

    for err in errors:
        err_ts = err.get("ts")
        if not err_ts:
            continue
        player_raw = err.get("player")
        if not player_raw:
            continue
        assert isinstance(player_raw, str), (
            f"error player must be a string, got {player_raw!r}"
        )
        player_decisions_for_error = player_decisions.get(player_raw)
        if not player_decisions_for_error:
            continue
        lo, hi = 0, len(player_decisions_for_error)
        while lo < hi:
            mid = (lo + hi) // 2
            if player_decisions_for_error[mid][0] <= err_ts:
                lo = mid + 1
            else:
                hi = mid
        if lo > 0:
            err["decisionIndex"] = player_decisions_for_error[lo - 1][1]
