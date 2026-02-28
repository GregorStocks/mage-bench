#!/usr/bin/env python3
"""Export a game log directory into a single JSON file for the website visualizer."""

import gzip
import json
import re
import sys
from datetime import datetime
from pathlib import Path

WEBSITE_GAMES_DIR = (
    Path(__file__).resolve().parent.parent / "website" / "public" / "games"
)
LOGS_DIR = Path.home() / ".mage-bench" / "logs"

FONT_TAG_RE = re.compile(r"<font[^>]*>|</font>")
OBJECT_ID_RE = re.compile(r"\s*\[[0-9a-f]{3,}\]")
DECKLIST_RE = re.compile(r"(?:SB:\s*)?(\d+)\s+\[([^:]+):([^\]]+)\]\s+(.+)")
LOST_GAME_RE = re.compile(r"^(.+?) has lost the game\.$")
WON_GAME_RE = re.compile(r"^(.+?) has won the game$")

# LLM event types to include in the website export
_LLM_EVENT_TYPES = {
    "game_start",
    "llm_response",
    "tool_call",
    "stall",
    "context_reset",
    "llm_error",
    "auto_pilot_mode",
}

# Size threshold: use .json.gz above 25 MiB (Cloudflare Pages file size limit)
_GZ_THRESHOLD = 25 * 1024 * 1024


def _strip_html(message: str) -> str:
    """Remove <font> tags and [hex_id] suffixes from action messages."""
    message = FONT_TAG_RE.sub("", message)
    message = OBJECT_ID_RE.sub("", message)
    return message.strip()


def _build_card_images(players_meta: list[dict]) -> dict[str, str]:
    """Build card name -> Scryfall small image URL map from decklists."""
    images = {}
    for player in players_meta:
        for entry in player.get("decklist", []):
            m = DECKLIST_RE.match(entry)
            if m:
                set_code = m.group(2).lower()
                card_num = m.group(3)
                card_name = m.group(4).strip()
                images[card_name] = (
                    f"https://api.scryfall.com/cards/{set_code}/{card_num}"
                    f"?format=image&version=small"
                )
    return images


_COMMANDER_DECK_TYPES = {
    "Variant Magic - Freeform Commander",
    "Variant Magic - Commander",
}


def _extract_commander(player_meta: dict) -> str | None:
    """Find commander name from decklist (SB: entries)."""
    for entry in player_meta.get("decklist", []):
        if entry.startswith("SB:"):
            m = DECKLIST_RE.match(entry)
            if m:
                return m.group(4).strip()
    return None


def _deck_name_from_path(deck_path: str) -> str | None:
    """Derive human-readable deck name from file path stem."""
    if not deck_path:
        return None
    return Path(deck_path).stem.replace("-", " ")


def _deck_display_name(player_meta: dict, deck_type: str) -> str | None:
    """Get display name for a player's deck.

    Prefers deck_name from game_meta (set by deck registry resolution).
    Falls back to legacy logic for old game_metas: commander card name
    for commander formats, filename stem for others.
    """
    # New: deck_name from registry
    if player_meta.get("deck_name"):
        return player_meta["deck_name"]
    # Legacy fallback for old game_metas
    if deck_type in _COMMANDER_DECK_TYPES:
        return _extract_commander(player_meta)
    return _deck_name_from_path(player_meta.get("deck_path", ""))


def compute_thinking_time(llm_events: list[dict]) -> dict[str, float]:
    """Compute per-player thinking time from sorted LLM events.

    For each consecutive pair of events, the time gap is attributed to the
    player of the earlier event. This approximates wall-clock time each player
    spent with priority (similar to a chess clock).

    Returns {player_name: total_seconds}.
    """
    thinking: dict[str, float] = {}
    for i in range(len(llm_events) - 1):
        player = llm_events[i].get("player", "")
        if not player:
            continue
        ts_a = llm_events[i].get("ts", "")
        ts_b = llm_events[i + 1].get("ts", "")
        if not ts_a or not ts_b:
            continue
        try:
            dt_a = datetime.fromisoformat(ts_a)
            dt_b = datetime.fromisoformat(ts_b)
        except ValueError:
            continue
        gap = (dt_b - dt_a).total_seconds()
        if gap > 0:
            thinking[player] = thinking.get(player, 0.0) + gap
    return thinking


def _read_llm_events(
    game_dir: Path,
) -> tuple[
    list[dict],
    dict[str, float],
    dict[str, list[str]],
    dict[str, tuple[int, int]],
    dict[str, float],
]:
    """Read LLM events from all *_llm.jsonl files.

    Returns (llm_events sorted by timestamp, {player_name: total_cost_usd},
    {player_name: available_tools}, {player_name: (ok_count, failed_count)},
    {player_name: thinking_time_secs}).
    """
    events = []
    player_costs: dict[str, float] = {}
    player_tools: dict[str, list[str]] = {}
    player_tool_calls: dict[str, tuple[int, int]] = {}

    for path in sorted(game_dir.glob("*_llm.jsonl")):
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = raw.get("type", "")
            player = raw.get("player", "")

            # Track per-player cost from game_end or cumulative_cost_usd
            if event_type == "game_end" and "total_cost_usd" in raw:
                player_costs[player] = raw["total_cost_usd"]
            elif "cumulative_cost_usd" in raw:
                player_costs[player] = raw["cumulative_cost_usd"]

            # Track per-player tools from game_start
            if event_type == "game_start" and "available_tools" in raw:
                player_tools[player] = raw["available_tools"]

            # Track per-player tool call success/failure
            if event_type == "tool_call" and player:
                ok, failed = player_tool_calls.get(player, (0, 0))
                is_failure = False
                result_str = raw.get("result", "")
                if result_str:
                    try:
                        result_obj = json.loads(result_str)
                        if (
                            isinstance(result_obj, dict)
                            and result_obj.get("success") is False
                        ):
                            is_failure = True
                    except (json.JSONDecodeError, TypeError):
                        pass
                if is_failure:
                    player_tool_calls[player] = (ok, failed + 1)
                else:
                    player_tool_calls[player] = (ok + 1, failed)

            if event_type not in _LLM_EVENT_TYPES:
                continue

            # Build the exported event with camelCase keys
            exported: dict = {
                "ts": raw.get("ts", ""),
                "seq": raw.get("seq"),
                "player": player,
                "type": event_type,
            }

            # game_seq: first-class field from pilot logging, or extracted
            # from the tool result JSON string for scripted/replay scenarios.
            game_seq = raw.get("game_seq")

            if event_type == "game_start":
                exported["model"] = raw.get("model", "")
                exported["availableTools"] = raw.get("available_tools", [])
            elif event_type == "llm_response":
                exported["reasoning"] = raw.get("reasoning", "")
                if raw.get("thinking"):
                    exported["thinking"] = raw["thinking"]
                if raw.get("tool_calls"):
                    exported["toolCalls"] = raw["tool_calls"]
                usage = raw.get("usage")
                if usage:
                    exported["usage"] = {
                        "promptTokens": usage.get("prompt_tokens", 0),
                        "completionTokens": usage.get("completion_tokens", 0),
                    }
                    if usage.get("cached_tokens"):
                        exported["usage"]["cachedTokens"] = usage["cached_tokens"]
                    if usage.get("reasoning_tokens"):
                        exported["usage"]["reasoningTokens"] = usage["reasoning_tokens"]
                if "cost_usd" in raw:
                    exported["costUsd"] = raw["cost_usd"]
            elif event_type == "tool_call":
                exported["tool"] = raw.get("tool", "")
                exported["args"] = raw.get("arguments", {})
                exported["result"] = raw.get("result", "")
                if "latency_ms" in raw:
                    exported["latencyMs"] = raw["latency_ms"]
                # Extract game_seq from result JSON if not already a top-level field
                if game_seq is None:
                    result_str = raw.get("result", "")
                    if result_str:
                        try:
                            result_obj = json.loads(result_str)
                            if isinstance(result_obj, dict):
                                game_seq = result_obj.get("game_seq")
                        except (json.JSONDecodeError, TypeError):
                            pass
            elif event_type == "stall":
                exported["turnsWithoutProgress"] = raw.get("turns_without_progress", 0)
                exported["lastTools"] = raw.get("last_tools", [])
            elif event_type == "context_reset":
                exported["reason"] = raw.get("reason", "")
            elif event_type == "llm_error":
                exported["errorType"] = raw.get("error_type", "")
                exported["errorMessage"] = raw.get("error_message", "")
            elif event_type == "auto_pilot_mode":
                exported["reason"] = raw.get("reason", "")

            if game_seq is not None:
                exported["gameSeq"] = game_seq

            events.append(exported)

    # Sort by timestamp
    events.sort(key=lambda e: e.get("ts", ""))

    player_thinking = compute_thinking_time(events)

    return events, player_costs, player_tools, player_tool_calls, player_thinking


def _read_llm_trace(game_dir: Path) -> list[dict]:
    """Read full LLM request/response traces from *_llm_trace.jsonl files.

    Returns trace events sorted by timestamp.
    """
    events = []
    for path in sorted(game_dir.glob("*_llm_trace.jsonl")):
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if raw.get("type") != "llm_call":
                continue
            request = raw.get("request", {})
            # Strip bulky repeated fields (system prompt, conversation history,
            # tool definitions) — they bloat the export by 100x+.
            request = {
                k: v for k, v in request.items() if k not in ("messages", "tools")
            }
            events.append(
                {
                    "ts": raw.get("ts", ""),
                    "seq": raw.get("seq"),
                    "player": raw.get("player", ""),
                    "request": request,
                    "response": raw.get("response", {}),
                }
            )
    events.sort(key=lambda e: e.get("ts", ""))
    return events


def _read_server_events(
    game_dir: Path,
) -> tuple[list[dict], list[dict], dict | None, str | None]:
    """Read events from server_game_events.jsonl.

    Returns (snapshots, actions, game_over_info, winner).
    """
    server_events_path = game_dir / "server_game_events.jsonl"
    assert server_events_path.exists(), f"No server_game_events.jsonl in {game_dir}"

    snapshots: list[dict] = []
    actions: list[dict] = []
    game_over: dict | None = None
    winner: str | None = None

    for line in server_events_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        event = json.loads(line)
        event_type = event.get("type")

        if event_type == "decision" and "state" in event:
            snap = dict(event["state"])
            snap["seq"] = event["seq"]
            snapshots.append(snap)
        elif event_type == "game_action":
            actions.append(
                {
                    "seq": event.get("seq", 0),
                    "message": _strip_html(event.get("message", "")),
                }
            )
        elif event_type == "turn_change":
            actions.append(
                {
                    "seq": event.get("seq", 0),
                    "type": "turn_change",
                    "turn": event["turn"],
                    "active_player": event.get("active_player"),
                }
            )
        elif event_type == "phase_change":
            actions.append(
                {
                    "seq": event.get("seq", 0),
                    "type": "phase_change",
                    "turn": event["turn"],
                    "phase": event.get("phase"),
                    "step": event.get("step"),
                    "active_player": event.get("active_player"),
                }
            )
        elif event_type == "game_end":
            game_over = {
                "seq": event.get("seq", 0),
                "message": event.get("winner", "") or "Game ended",
            }
            winner = event.get("winner")
            if "state" in event:
                snap = dict(event["state"])
                snap["seq"] = event["seq"]
                snapshots.append(snap)

    return snapshots, actions, game_over, winner


def _parse_json(s: str) -> dict:
    """Parse a JSON string, returning {} on failure."""
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return {}


def _is_decision_source(event: dict) -> bool:
    """Check if a tool_call event is a decision source.

    A decision source is a pass_priority or get_action_choices tool_call
    whose result has action_pending=true.
    """
    if event.get("type") != "tool_call":
        return False
    tool = event.get("tool")
    if tool not in ("pass_priority", "get_action_choices"):
        return False
    result = _parse_json(event.get("result", ""))
    return bool(result.get("action_pending"))


def _is_v1_decision_source(event: dict) -> bool:
    """Check if a tool_call event is a v1 decision source.

    In v1 (harnessEpoch < 20), decisions anchor on get_action_choices
    events with action_pending=true.
    """
    if event.get("type") != "tool_call":
        return False
    if event.get("tool") != "get_action_choices":
        return False
    result = _parse_json(event.get("result", ""))
    return result.get("action_pending", True)


def _resolve_chosen_index(
    chosen_args: dict, available_choices: list, action_result: dict
) -> object | None:
    """Resolve chosen_index from choose_action args.

    Tries arg keys in order: index, answer, amount, id.
    Falls back to parsing "selected_N" from action_taken.
    """
    if "index" in chosen_args:
        return chosen_args["index"]
    if "answer" in chosen_args:
        return chosen_args["answer"]
    if "amount" in chosen_args:
        return chosen_args["amount"]
    if "id" in chosen_args:
        target_id = chosen_args["id"]
        for ci, c in enumerate(available_choices):
            if isinstance(c, dict) and c.get("id") == target_id:
                return ci
    # Fallback: parse "selected_N" from action_taken
    taken = action_result.get("action_taken", "")
    if taken.startswith("selected_"):
        try:
            return int(taken.split("_", 1)[1])
        except (ValueError, IndexError):
            pass
    return None


def _find_snapshot_index_by_seq(snapshots: list[dict], seq: int) -> int | None:
    """Find the index of the nearest snapshot at or before the given game seq."""
    best: int | None = None
    for i, snap in enumerate(snapshots):
        if snap.get("seq", 0) <= seq:
            best = i
        else:
            break
    return best


def _find_snapshot_index_by_ts(snapshots: list[dict], ts: str) -> int | None:
    """Find the index of the nearest snapshot at or before the given timestamp."""
    best: int | None = None
    for i, snap in enumerate(snapshots):
        if snap.get("ts", "") <= ts:
            best = i
        else:
            break
    return best


def _extract_pilot_context(choices_result: dict) -> dict:
    """Extract pilot-specific overlay data from a tool result."""
    ctx: dict = {}
    if "untapped_lands" in choices_result:
        ctx["untappedLands"] = choices_result["untapped_lands"]
    if "land_drops_used" in choices_result:
        ctx["landDropsUsed"] = choices_result["land_drops_used"]
    if "combat_phase" in choices_result:
        ctx["combatPhase"] = choices_result["combat_phase"] or None
    if "already_attacking" in choices_result:
        ctx["alreadyAttacking"] = choices_result["already_attacking"]
    if "incoming_attackers" in choices_result:
        ctx["incomingAttackers"] = choices_result["incoming_attackers"]
    # Extract playable card IDs from board hand.
    # board is a list of player objects (not {players: [...]}).
    board = choices_result.get("board", [])
    if isinstance(board, dict):
        board = board.get("players", [])
    playable_ids: list[str] = []
    for p in board if isinstance(board, list) else []:
        for card in p.get("hand", []) if isinstance(p, dict) else []:
            if isinstance(card, dict) and card.get("playable"):
                card_id = card.get("id", "")
                if card_id:
                    playable_ids.append(card_id)
    if playable_ids:
        ctx["playableCards"] = playable_ids
    return ctx


_CAST_PROMPT_PREFIXES = (
    "Play spells and abilities",
    "Play instants and activated abilities",
)


def _find_spell_cancelled_seqs(llm_events: list[dict]) -> list[tuple[str, int]]:
    """Find (player, event_index) pairs for [System] Spell cancelled messages.

    Returns the index of the tool_call event BEFORE the one containing the
    cancel message (since the cancellation happens during a blocking call but
    only surfaces in the result).
    """
    last_idx: dict[str, int] = {}
    cancelled: list[tuple[str, int]] = []
    for i, ev in enumerate(llm_events):
        if ev.get("type") != "tool_call":
            continue
        player = ev.get("player", "")
        result_str = ev.get("result", "")
        if "[System] Spell cancelled" not in result_str:
            last_idx[player] = i
            continue
        result = _parse_json(result_str)
        for msg in result.get("recent_chat", []):
            if "[System] Spell cancelled" in str(msg):
                cancelled.append((player, last_idx.get(player, i)))
                break
        last_idx[player] = i
    return cancelled


def _mark_rolled_back_casts(
    decisions: list[dict], cancelled: list[tuple[str, int]]
) -> None:
    """Mark rolled-back cast sequences on canonical decisions.

    Uses llmEventIndices to match cancel events to decisions.
    """
    # Build set of (player, event_index) for fast lookup
    cancel_set: dict[str, set[int]] = {}
    for player, idx in cancelled:
        cancel_set.setdefault(player, set()).add(idx)

    for player, cancel_idx in sorted(cancelled, key=lambda x: x[1]):
        for j in range(len(decisions) - 1, -1, -1):
            d = decisions[j]
            if d["player"] != player:
                continue
            # Skip decisions whose last llm event is after the cancel point
            indices = d.get("llmEventIndices", [])
            if not indices:
                continue
            if indices[0] > cancel_idx:
                continue
            if d.get("castRolledBack"):
                break
            msg = d.get("message", "")
            if msg.startswith(_CAST_PROMPT_PREFIXES):
                d["castRolledBack"] = True
                break


# Messages where the player can always decline (pass/not attack/not block).
_PASS_ALLOWED_PREFIXES = (
    "Play ",
    "Choose spell or ability",
    "Choose ability",
    "Select attacker",
    "Select blocker",
)


def _is_forced(response_type: str, message: str, choices: list) -> bool:
    """Determine if a decision is truly forced (no meaningful choice).

    Boolean questions always have yes/no.
    Single-choice selects where the player can pass are not forced.
    """
    if response_type == "boolean":
        return False
    n = len(choices)
    if n == 0:
        return True
    if n == 1:
        return not message.startswith(_PASS_ALLOWED_PREFIXES)
    return False


def _build_decisions(
    snapshots: list[dict],
    actions: list[dict],
    llm_events: list[dict],
    harness_epoch: int,
) -> list[dict]:
    """Build canonical decision records from export data.

    Handles both v1 (harnessEpoch < 20, get_action_choices anchored) and
    v2 (harnessEpoch >= 20, pass_priority/get_action_choices with action_pending).
    """
    is_v2 = harness_epoch >= 20

    # Collect decision source events with their indices
    decision_sources: list[tuple[int, dict]] = []
    for i, event in enumerate(llm_events):
        if is_v2:
            if _is_decision_source(event):
                decision_sources.append((i, event))
        else:
            if _is_v1_decision_source(event):
                decision_sources.append((i, event))

    decisions: list[dict] = []

    for ds_idx, (event_idx, source_event) in enumerate(decision_sources):
        choices_result = _parse_json(source_event.get("result", ""))
        player = source_event.get("player", "")

        available_choices = choices_result.get("choices", [])
        response_type = choices_result.get("response_type", "")
        action_type = choices_result.get("action_type", "")
        message = choices_result.get("message", "")

        # Collect llmEventIndices and find choose_action
        llm_event_indices: list[int] = [event_idx]
        chosen_index = None
        chosen_args: dict = {}
        action_result: dict = {}

        for j in range(event_idx + 1, len(llm_events)):
            ev = llm_events[j]
            if ev.get("player") != player:
                continue

            if ev.get("type") == "llm_response":
                llm_event_indices.append(j)

            if ev.get("type") == "tool_call" and ev.get("tool") == "choose_action":
                llm_event_indices.append(j)
                chosen_args = ev.get("args", {})
                action_result = _parse_json(ev.get("result", ""))
                chosen_index = _resolve_chosen_index(
                    chosen_args, available_choices, action_result
                )
                break

            # Stop at next decision source for this player
            if is_v2 and _is_decision_source(ev):
                break
            if not is_v2 and _is_v1_decision_source(ev):
                break

        # Find matching snapshot
        choices_seq = source_event.get("gameSeq") or 0
        if choices_seq:
            snap_idx = _find_snapshot_index_by_seq(snapshots, choices_seq)
        else:
            choices_ts = source_event.get("ts", "")
            snap_idx = (
                _find_snapshot_index_by_ts(snapshots, choices_ts)
                if choices_ts
                else None
            )

        # Get turn/phase/step from snapshot
        snap = snapshots[snap_idx] if snap_idx is not None else {}
        snap_idx_val = snap_idx if snap_idx is not None else 0

        # Collect subsequent game actions
        action_seq = choices_seq
        # Find the gameSeq of the choose_action event if present
        for j in llm_event_indices:
            ev = llm_events[j]
            if ev.get("type") == "tool_call" and ev.get("tool") == "choose_action":
                action_seq = ev.get("gameSeq", choices_seq)
                break

        next_choices_seq = 0
        if ds_idx + 1 < len(decision_sources):
            next_choices_seq = decision_sources[ds_idx + 1][1].get("gameSeq", 0)

        subsequent: list[str] = []
        for a in actions:
            a_seq = a.get("seq", 0)
            if a_seq <= action_seq:
                continue
            if next_choices_seq and a_seq > next_choices_seq:
                break
            if a.get("message"):
                subsequent.append(a["message"])
            if len(subsequent) >= 5:
                break

        # Build canonical decision
        decision: dict = {
            "index": len(decisions),
            "snapshotIndex": snap_idx_val,
            "player": player,
            "turn": snap.get("turn", 0),
            "phase": snap.get("phase"),
            "step": snap.get("step"),
            "actionType": action_type,
            "responseType": response_type,
            "message": message,
            "choices": available_choices,
            "choiceCount": len(available_choices),
            "isForced": _is_forced(response_type, message, available_choices),
            "chosen": chosen_index,
            "chosenArgs": chosen_args,
            "actionResult": action_result,
            "llmEventIndices": llm_event_indices,
            "subsequentActions": subsequent,
        }

        # Add pilot context if available
        pilot_ctx = _extract_pilot_context(choices_result)
        if pilot_ctx:
            decision["pilotContext"] = pilot_ctx

        decisions.append(decision)

    # Detect and mark rolled-back casts
    cancelled = _find_spell_cancelled_seqs(llm_events)
    if cancelled:
        _mark_rolled_back_casts(decisions, cancelled)

    return decisions


def build_export(game_dir: Path) -> dict:
    """Build the export data dict from a game directory.

    Reads server_game_events.jsonl (version 2 format).
    """
    meta_path = game_dir / "game_meta.json"

    # Load metadata
    meta: dict = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())

    snapshots, actions, game_over, winner = _read_server_events(game_dir)

    # Read LLM logs
    llm_events, player_costs, player_tools, player_tool_calls, player_thinking = (
        _read_llm_events(game_dir)
    )
    llm_trace = _read_llm_trace(game_dir)

    # Build card images map from decklists
    card_images = _build_card_images(meta.get("players", []))

    # Extract game metadata
    game_id = game_dir.name
    total_turns = max((s.get("turn", 0) for s in snapshots), default=0)

    # Winner extraction for spectator-based exports
    if not winner and game_over:
        msg = game_over["message"]
        m = re.match(r"Player (.+?) is the winner", msg)
        if m:
            winner = m.group(1)
    if not winner:
        for a in actions:
            m = WON_GAME_RE.match(a.get("message", ""))
            if m:
                winner = m.group(1)
                break

    # Extract placement from elimination order
    player_names = [p.get("name", "?") for p in meta.get("players", [])]
    eliminations = []
    for a in actions:
        m = LOST_GAME_RE.match(a.get("message", ""))
        if m:
            eliminations.append(m.group(1))
    placements: dict[str, int] = {}
    if winner:
        placements[winner] = 1
        for i, name in enumerate(reversed(eliminations)):
            placements[name] = i + 2
    elif eliminations:
        surviving = [n for n in player_names if n not in eliminations]
        for name in surviving:
            placements[name] = 1
        for i, name in enumerate(reversed(eliminations)):
            placements[name] = len(surviving) + i + 1

    # Derive winner from placements if not already set
    if not winner:
        first_place = [n for n, p in placements.items() if p == 1]
        if len(first_place) == 1:
            winner = first_place[0]

    players_summary = []
    for p in meta.get("players", []):
        name = p.get("name", "?")
        entry: dict = {
            "name": name,
            "type": p.get("type", "?"),
            "deckName": _deck_display_name(p, meta.get("deck_type", "")),
        }
        if p.get("deck_strategy"):
            entry["deckStrategy"] = p["deck_strategy"]
        if p.get("model"):
            entry["model"] = p["model"]
        if p.get("reasoning_effort"):
            entry["reasoningEffort"] = p["reasoning_effort"]
        if name in player_costs:
            entry["totalCostUsd"] = round(player_costs[name], 4)
        if name in placements:
            entry["placement"] = placements[name]
        if name in player_tools:
            entry["tools"] = player_tools[name]
        if name in player_tool_calls:
            ok, failed = player_tool_calls[name]
            entry["toolCallsOk"] = ok
            entry["toolCallsFailed"] = failed
        if name in player_thinking:
            entry["thinkingTimeSecs"] = round(player_thinking[name], 1)
        players_summary.append(entry)

    # Build output
    output: dict = {
        "version": 2,
        "id": game_id,
        "timestamp": meta.get("timestamp", ""),
        "gameType": meta.get("game_type", ""),
        "deckType": meta.get("deck_type", ""),
        "totalTurns": total_turns,
        "winner": winner,
        "players": players_summary,
        "cardImages": card_images,
        "snapshots": snapshots,
        "actions": actions,
        "llmEvents": llm_events,
        "llmTrace": llm_trace,
        "gameOver": game_over,
    }
    if meta.get("harness_epoch") is not None:
        output["harnessEpoch"] = meta["harness_epoch"]
    if meta.get("youtube_url"):
        output["youtubeUrl"] = meta["youtube_url"]

    # Build canonical decisions
    harness_epoch = meta.get("harness_epoch", 0)
    decisions = _build_decisions(snapshots, actions, llm_events, harness_epoch)
    if decisions:
        output["decisions"] = decisions

    _validate_export(output)
    return output


# Fields that build_export() always emits. harnessEpoch and youtubeUrl are
# conditional on metadata; annotations and blunderScriptVersion are added by
# annotate_game.py after export. See schemas/game-export-v2.schema.json for
# the full schema including those downstream fields.
_BUILD_EXPORT_REQUIRED = {
    "version",
    "id",
    "timestamp",
    "gameType",
    "deckType",
    "totalTurns",
    "winner",
    "players",
    "cardImages",
    "snapshots",
    "actions",
    "llmEvents",
    "llmTrace",
    "gameOver",
}


def _validate_export(data: dict) -> None:
    """Assert the export has the expected top-level structure.

    Lightweight runtime check — no jsonschema dependency. Catches missing
    required fields and wrong version. The full JSON Schema validation
    runs in tests (test_export_schema.py).
    """
    assert data.get("version") == 2, f"Expected version 2, got {data.get('version')}"
    missing = _BUILD_EXPORT_REQUIRED - set(data.keys())
    assert not missing, f"Export missing required fields: {missing}"
    assert isinstance(data["players"], list), "players must be a list"
    assert isinstance(data["snapshots"], list), "snapshots must be a list"
    assert isinstance(data["actions"], list), "actions must be a list"
    assert isinstance(data["llmEvents"], list), "llmEvents must be a list"
    for i, p in enumerate(data["players"]):
        assert "name" in p, f"Player {i} missing 'name'"
        assert "type" in p, f"Player {i} missing 'type'"


def export_game(game_dir: Path, website_games_dir: Path) -> Path:
    """Export a game directory to a website JSON file. Returns the output path."""
    output = build_export(game_dir)
    game_id = output["id"]

    website_games_dir.mkdir(parents=True, exist_ok=True)

    json_str = json.dumps(output, indent=2, ensure_ascii=False)
    json_bytes = json_str.encode()
    if len(json_bytes) > _GZ_THRESHOLD:
        output_path = website_games_dir / f"{game_id}.json.gz"
        output_path.write_bytes(gzip.compress(json_bytes))
        # Clean up uncompressed file if it exists
        json_path = website_games_dir / f"{game_id}.json"
        if json_path.exists():
            json_path.unlink()
    else:
        output_path = website_games_dir / f"{game_id}.json"
        output_path.write_bytes(json_bytes)
        # Clean up compressed file if it exists
        gz_path = website_games_dir / f"{game_id}.json.gz"
        if gz_path.exists():
            gz_path.unlink()

    return output_path


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <game_id> [website_games_dir]")
        print(f"  game_id: directory name under {LOGS_DIR}")
        sys.exit(1)

    game_id = sys.argv[1]
    game_dir = LOGS_DIR / game_id
    if not game_dir.is_dir():
        print(f"Error: {game_dir} is not a directory")
        sys.exit(1)

    games_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else WEBSITE_GAMES_DIR
    output_path = export_game(game_dir, games_dir)
    size_kb = output_path.stat().st_size // 1024
    print(f"Exported {game_id} -> {output_path} ({size_kb} KB)")


if __name__ == "__main__":
    main()
