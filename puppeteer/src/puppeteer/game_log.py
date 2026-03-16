"""Structured game logging: JSONL writer, decklist parser, post-game merge."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from zoneinfo import ZoneInfo


class GameLogWriter:
    """Appends JSONL events to {player}_llm.jsonl."""

    _TZ = ZoneInfo("America/Los_Angeles")

    def __init__(self, game_dir: Path, player_name: str, suffix: str = "llm") -> None:
        self._path = game_dir / f"{player_name}_{suffix}.jsonl"
        self._player = player_name
        self._seq = 0
        self._last_cumulative_cost_usd = 0.0
        self._file = open(self._path, "a")

    def emit(self, event_type: str, **fields: object) -> None:
        self._seq += 1
        ts = datetime.now(self._TZ).isoformat(timespec="microseconds")
        if "cumulative_cost_usd" in fields:
            cost_value = fields["cumulative_cost_usd"]
            assert isinstance(cost_value, (str, int, float)) and not isinstance(cost_value, bool), (
                f"cumulative_cost_usd must be a string or number, got {cost_value!r}"
            )
            try:
                self._last_cumulative_cost_usd = float(cost_value)
            except ValueError as exc:
                raise AssertionError(f"cumulative_cost_usd must parse as a float, got {cost_value!r}") from exc
        event = {
            "ts": ts,
            "seq": self._seq,
            "type": event_type,
            "player": self._player,
            **fields,
        }
        self._file.write(json.dumps(event, separators=(",", ":")) + "\n")
        self._file.flush()

    def last_cumulative_cost_usd(self) -> float:
        return self._last_cumulative_cost_usd

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> GameLogWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()


def read_decklist(deck_path: Path) -> list[str]:
    """Parse a .dck file into a list of card entries (e.g. '1 Sol Ring')."""
    entries = []
    for line in deck_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("//"):
            # .dck format: "1 [SET:123] Card Name" or "SB: 1 [SET:123] Card Name"
            entries.append(line)
    return entries


def _parse_ts(value: str) -> float | None:
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def merge_game_log(game_dir: Path) -> None:
    """Merge game event files into game.jsonl, sorted by game_seq then timestamp.

    Merges: server_game_events.jsonl, game_events.jsonl, and all *_llm.jsonl files.
    Events with game_seq sort before events without, ordered by game_seq ascending.
    Events without game_seq fall back to timestamp ordering.
    """
    all_events: list[tuple[int | None, float | None, int, str]] = []
    order = 0

    # Collect from all source files
    for pattern in ["server_game_events.jsonl", "game_events.jsonl", "*_llm.jsonl"]:
        for path in game_dir.glob(pattern):
            for _line_num, line in enumerate(path.read_text().splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    # game_seq from server log (uses "seq") or observer's game_seq field
                    if path.name == "server_game_events.jsonl":
                        game_seq: int | None = event.get("seq")
                    else:
                        game_seq = event.get("game_seq")
                    ts_value = _parse_ts(event.get("ts", ""))
                    all_events.append((game_seq, ts_value, order, line))
                    order += 1
                except json.JSONDecodeError:
                    pass

    # Sort: events with game_seq first (ascending), then by timestamp, then by read order
    def sort_key(item: tuple[int | None, float | None, int, str]) -> tuple[bool, int, bool, float, int]:
        game_seq, ts_value, read_order, _ = item
        return (
            game_seq is None,  # game_seq events first
            game_seq or 0,  # ascending game_seq
            ts_value is None,  # timestamp events before no-timestamp
            ts_value or 0.0,  # ascending timestamp
            read_order,  # stable tiebreaker
        )

    all_events.sort(key=sort_key)

    # Write merged file
    merged_path = game_dir / "game.jsonl"
    with open(merged_path, "w") as f:
        for _, _, _, line in all_events:
            f.write(line + "\n")
