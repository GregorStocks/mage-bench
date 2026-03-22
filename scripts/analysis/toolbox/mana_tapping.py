#!/usr/bin/env python3
"""Analyze mana tapping behavior from .json.gz game exports.

Reports mana_plan/auto_tap usage, auto-tap effectiveness, GAME_CHOOSE_ABILITY
handling, and spell cancellations across all players/models.

Usage:
    uv run python scripts/analysis/toolbox/mana_tapping.py <game.json.gz>
    uv run python scripts/analysis/toolbox/mana_tapping.py <directory-of-gz-files>
"""

import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from magebench.game.game_export_types import LlmEvent, is_pilot_player
from scripts.analysis.blunder_eval_common import load_game


@dataclass
class PlayerStats:
    """Per-player mana tapping statistics from a single game."""

    player: str
    model: str
    # mana_plan usage
    mana_plan_used: int = 0
    mana_plan_success: int = 0
    mana_plan_failed: int = 0
    # auto_tap usage
    auto_tap_used: int = 0
    # GAME_PLAY_MANA deferrals (auto-tapper failed, LLM asked)
    mana_deferred: int = 0
    mana_deferred_success: int = 0
    mana_deferred_failed: int = 0
    mana_deferred_cancelled: int = 0
    # GAME_CHOOSE_ABILITY
    choose_ability: int = 0
    choose_ability_success: int = 0
    choose_ability_failed: int = 0
    # Spell cancellations
    spells_cancelled: int = 0
    # Errors from mana_plan
    mana_plan_errors: list[str] = field(default_factory=list)


def analyze_game(gz_path: str) -> list[PlayerStats]:
    """Analyze a single game export and return per-player stats."""
    data = load_game(gz_path)
    game_id = data.id

    # Build player -> model mapping
    player_models: dict[str, str] = {}
    for p in data.players:
        if not is_pilot_player(p):
            continue
        player_models[p.name] = p.model

    # Initialize stats per player
    stats: dict[str, PlayerStats] = {}
    for name, model in player_models.items():
        stats[name] = PlayerStats(player=name, model=model)

    events = data.llm_events

    for i, e in enumerate(events):
        if e.type != "tool_call":
            continue

        tool = e.tool
        player = e.player
        assert player in stats, (
            f"{game_id}: tool_call event for unknown pilot player {player!r}"
        )
        ps = stats[player]

        # --- choose_action: check for mana_plan, auto_tap, spell cancellations ---
        if tool == "choose_action":
            args = e.args
            result_str = e.result
            try:
                result = json.loads(result_str)
            except (json.JSONDecodeError, TypeError):
                result = {}

            success = result.get("success", False)

            if "mana_plan" in args:
                ps.mana_plan_used += 1
                if success:
                    ps.mana_plan_success += 1
                else:
                    ps.mana_plan_failed += 1
                    error = result.get("error")
                    if error:
                        ps.mana_plan_errors.append(error[:150])

            if args.get("auto_tap") is True:
                ps.auto_tap_used += 1

            action_taken = result.get("action_taken")
            if action_taken and "cancelled_spell" in str(action_taken):
                ps.spells_cancelled += 1

        # --- get_action_choices: track GAME_PLAY_MANA and GAME_CHOOSE_ABILITY ---
        if tool == "get_action_choices":
            result_str = e.result
            try:
                result = json.loads(result_str)
            except json.JSONDecodeError:
                continue

            action_type = result.get("action_type")

            if action_type == "GAME_PLAY_MANA":
                ps.mana_deferred += 1
                # Look ahead for the choose_action response
                _track_followup(events, i, player, ps, "mana_deferred")

            elif action_type == "GAME_CHOOSE_ABILITY":
                ps.choose_ability += 1
                _track_followup(events, i, player, ps, "choose_ability")

    return list(stats.values())


def _track_followup(
    events: list[LlmEvent],
    start_idx: int,
    player: str,
    ps: PlayerStats,
    prefix: str,
) -> None:
    """Look ahead from a get_action_choices to find the corresponding choose_action."""
    for j in range(start_idx + 1, min(start_idx + 20, len(events))):
        ev = events[j]
        if ev.player != player:
            continue
        if ev.type == "tool_call" and ev.tool == "choose_action":
            result_str = ev.result
            try:
                result = json.loads(result_str)
            except json.JSONDecodeError:
                setattr(ps, f"{prefix}_failed", getattr(ps, f"{prefix}_failed") + 1)
                return
            if result.get("success"):
                action = result.get("action_taken")
                if action and "cancelled_spell" in str(action):
                    if prefix == "mana_deferred":
                        ps.mana_deferred_cancelled += 1
                    else:
                        raise AssertionError(
                            f"Unexpected cancelled_spell follow-up for {prefix}: {result!r}"
                        )
                else:
                    setattr(
                        ps,
                        f"{prefix}_success",
                        getattr(ps, f"{prefix}_success") + 1,
                    )
            else:
                setattr(ps, f"{prefix}_failed", getattr(ps, f"{prefix}_failed") + 1)
            return
        # Stop if we hit another get_action_choices from this player
        if ev.type == "tool_call" and ev.tool == "get_action_choices":
            return


def _print_section(title: str) -> None:
    print(f"\n=== {title} ===")


def report(all_stats: list[PlayerStats]) -> None:
    """Print aggregated report across all games."""
    if not all_stats:
        print("No data to report.")
        return

    # Aggregate by model
    model_agg: dict[str, PlayerStats] = {}
    for ps in all_stats:
        if ps.model not in model_agg:
            model_agg[ps.model] = PlayerStats(player="", model=ps.model)
        agg = model_agg[ps.model]
        agg.mana_plan_used += ps.mana_plan_used
        agg.mana_plan_success += ps.mana_plan_success
        agg.mana_plan_failed += ps.mana_plan_failed
        agg.auto_tap_used += ps.auto_tap_used
        agg.mana_deferred += ps.mana_deferred
        agg.mana_deferred_success += ps.mana_deferred_success
        agg.mana_deferred_failed += ps.mana_deferred_failed
        agg.mana_deferred_cancelled += ps.mana_deferred_cancelled
        agg.choose_ability += ps.choose_ability
        agg.choose_ability_success += ps.choose_ability_success
        agg.choose_ability_failed += ps.choose_ability_failed
        agg.spells_cancelled += ps.spells_cancelled
        agg.mana_plan_errors.extend(ps.mana_plan_errors)

    # Count player-games per model
    model_games: Counter[str] = Counter(ps.model for ps in all_stats)

    total_mp = sum(a.mana_plan_used for a in model_agg.values())
    total_mp_ok = sum(a.mana_plan_success for a in model_agg.values())
    total_mp_fail = sum(a.mana_plan_failed for a in model_agg.values())

    # --- Section 1: mana_plan Usage ---
    _print_section("mana_plan Usage")
    print(f"  Total uses: {total_mp} ({total_mp_ok} succeeded, {total_mp_fail} failed)")
    if total_mp > 0:
        print("  By model:")
        for model in sorted(model_agg):
            a = model_agg[model]
            if a.mana_plan_used > 0:
                print(
                    f"    {model}: {a.mana_plan_used} ({a.mana_plan_success} ok, {a.mana_plan_failed} failed)"
                )
        # Show error reasons
        all_errors: list[str] = []
        for a in model_agg.values():
            all_errors.extend(a.mana_plan_errors)
        if all_errors:
            error_counts = Counter(all_errors)
            print("  Failure reasons:")
            for err, cnt in error_counts.most_common(5):
                print(f"    ({cnt}x) {err}")

    # --- Section 2: auto_tap Usage ---
    total_at = sum(a.auto_tap_used for a in model_agg.values())
    _print_section("auto_tap Usage")
    print(f"  Total uses: {total_at}")
    if total_at > 0:
        print("  By model:")
        for model in sorted(model_agg):
            a = model_agg[model]
            if a.auto_tap_used > 0:
                print(f"    {model}: {a.auto_tap_used}")

    # --- Section 3: Auto-Tap Effectiveness ---
    total_def = sum(a.mana_deferred for a in model_agg.values())
    total_def_ok = sum(a.mana_deferred_success for a in model_agg.values())
    total_def_fail = sum(a.mana_deferred_failed for a in model_agg.values())
    total_def_cancel = sum(a.mana_deferred_cancelled for a in model_agg.values())
    _print_section("Auto-Tap Effectiveness (GAME_PLAY_MANA deferrals to LLM)")
    print(f"  Total deferrals: {total_def}")
    if total_def > 0:
        print(
            f"  LLM handled: {total_def_ok} success, {total_def_fail} failed, {total_def_cancel} cancelled"
        )
        print("  By model:")
        for model in sorted(model_agg):
            a = model_agg[model]
            if a.mana_deferred > 0:
                print(
                    f"    {model}: {a.mana_deferred} "
                    f"({a.mana_deferred_success} ok, {a.mana_deferred_failed} fail, "
                    f"{a.mana_deferred_cancelled} cancel)"
                )

    # --- Section 4: GAME_CHOOSE_ABILITY ---
    total_ca = sum(a.choose_ability for a in model_agg.values())
    total_ca_ok = sum(a.choose_ability_success for a in model_agg.values())
    total_ca_fail = sum(a.choose_ability_failed for a in model_agg.values())
    _print_section("GAME_CHOOSE_ABILITY Handling")
    print(f"  Total: {total_ca}")
    if total_ca > 0:
        print(f"  Success: {total_ca_ok}, Failed: {total_ca_fail}")
        print("  By model:")
        for model in sorted(model_agg):
            a = model_agg[model]
            if a.choose_ability > 0:
                print(
                    f"    {model}: {a.choose_ability} ({a.choose_ability_success} ok, {a.choose_ability_failed} fail)"
                )

    # --- Section 5: Spell Cancellations ---
    total_cancel = sum(a.spells_cancelled for a in model_agg.values())
    _print_section("Spell Cancellations")
    print(f"  Total: {total_cancel}")
    if total_cancel > 0:
        print("  By model:")
        for model in sorted(model_agg):
            a = model_agg[model]
            if a.spells_cancelled > 0:
                print(f"    {model}: {a.spells_cancelled}")

    # --- Section 6: Per-Model Summary ---
    _print_section("Per-Model Summary")
    header = f"  {'Model':<40} {'Games':>5} {'ManaPlan':>9} {'AutoTap':>8} {'Deferred':>9} {'Ability':>8} {'Cancel':>7}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for model in sorted(model_agg):
        a = model_agg[model]
        games = model_games[model]
        mp = f"{a.mana_plan_success}/{a.mana_plan_used}" if a.mana_plan_used else "-"
        at = str(a.auto_tap_used) if a.auto_tap_used else "-"
        de = f"{a.mana_deferred_success}/{a.mana_deferred}" if a.mana_deferred else "-"
        ca = (
            f"{a.choose_ability_success}/{a.choose_ability}"
            if a.choose_ability
            else "-"
        )
        cn = str(a.spells_cancelled) if a.spells_cancelled else "-"
        print(f"  {model:<40} {games:>5} {mp:>9} {at:>8} {de:>9} {ca:>8} {cn:>7}")


def main(path: str) -> None:
    """Analyze a single game export or a directory of them."""
    if os.path.isdir(path):
        p = Path(path)
        gz_files = sorted(list(p.glob("*.json5.gz")) + list(p.glob("*.json5")))
        assert gz_files, f"No game files found in {path}"
        print(f"Analyzing {len(gz_files)} games from {path}")
        all_stats: list[PlayerStats] = []
        for gz in gz_files:
            all_stats.extend(analyze_game(str(gz)))
    else:
        print(f"Analyzing {path}")
        all_stats = analyze_game(path)

    report(all_stats)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <game.json.gz | directory>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
