"""Mutable pilot loop state and board-cursor helpers."""

import json
from dataclasses import dataclass, field

from puppeteer.pilot_rendering import _build_reset_message, _extract_last_reasoning

_BOARD_CURSOR_TOOLS = frozenset({"pass_priority", "get_action_choices"})


class BoardCursorTracker:
    """Tracks board_cursor across tool calls for board state dedup."""

    def __init__(self) -> None:
        self.cursor: int | None = None

    def inject(self, tool_name: str, args: dict) -> None:
        """Inject board_cursor into args if applicable."""
        if tool_name in _BOARD_CURSOR_TOOLS and self.cursor is not None:
            args["board_cursor"] = self.cursor

    def extract(self, result_text: str) -> None:
        """Extract board_cursor from a tool result string."""
        try:
            data = json.loads(result_text)
            if isinstance(data, dict) and "board_cursor" in data:
                self.cursor = data["board_cursor"]
        except (json.JSONDecodeError, TypeError):
            pass

    def reset(self) -> None:
        """Force full board on the next call (e.g. after context reset)."""
        self.cursor = None


@dataclass
class PilotLoopState:
    """Mutable state for the pilot loop."""

    history: list[dict]
    state_summary: str = ""
    cumulative_cost: float = 0.0
    empty_responses: int = 0
    last_was_empty: bool = False
    consecutive_timeouts: int = 0
    consecutive_empty_choices: int = 0
    turns_without_progress: int = 0
    consecutive_pass_errors: int = 0
    last_pass_error_msg: str = ""
    consecutive_truncations: int = 0
    consecutive_empty_errors: int = 0
    last_game_seq: int | None = None
    board_tracker: BoardCursorTracker = field(default_factory=BoardCursorTracker)
    last_board: list[dict] | None = None
    current_game_turn: int = 0
    last_chat_turn: int = 0
    seen_oracle_cards: set[str] = field(default_factory=set)
    cache_breakpoint_idx: int | None = None
    render_counter: int = 0


@dataclass
class PilotTurnState:
    """Per-response tool execution state used for stall detection."""

    had_successful_action: bool = False
    had_actionable_opportunity: bool = False
    tools_called: set[str] = field(default_factory=set)
    chat_messages_this_turn: int = 0


def _reset_render_cache(state: PilotLoopState) -> None:
    """Drop cached prompt metadata after a context reset."""
    state.state_summary = ""
    state.cache_breakpoint_idx = None
    state.render_counter = 0


def _reset_context(
    state: PilotLoopState,
    base_text: str,
    *,
    reset_board_context: bool,
) -> None:
    """Reset the conversation while preserving the last assistant reasoning."""
    last_reasoning = _extract_last_reasoning(state.history)
    state.history = [
        {
            "role": "user",
            "content": _build_reset_message(base_text, last_reasoning),
        },
    ]
    _reset_render_cache(state)
    state.seen_oracle_cards.clear()
    if reset_board_context:
        state.board_tracker.reset()
        state.last_board = None
