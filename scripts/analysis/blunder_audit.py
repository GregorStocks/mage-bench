#!/usr/bin/env python3
"""Interactive CLI for auditing blunder ground truth entries.

Subcommands:
    audit   Iterate unaudited plays, collect human verdicts (default)
    add     Manually add a play from a game viewer URL

Usage:
    uv run --project puppeteer python scripts/analysis/blunder_audit.py [--game GAME_ID]
    uv run --project puppeteer python scripts/analysis/blunder_audit.py add URL
"""

import argparse
import atexit
import socket
import subprocess
import textwrap
import time
from collections.abc import Sequence
from urllib.parse import parse_qs, urlparse

from magebench.analysis.blunder.blunder_analysis import (
    BLUNDER_SCRIPT_VERSION,
    OPUS_MODEL,
    evaluate_one_decision,
    init_api,
    load_game_context,
)
from magebench.analysis.blunder.blunder_eval_common import (
    REPO_ROOT,
    chosen_display,
    compute_aftermath_index,
    export_record_name,
    game_path_for_id,
    load_game,
    load_game_ground_truth,
    load_ground_truth,
    lookup_annotation_for_decision,
    make_audited_entry,
    save_game_ground_truth,
    validate_game_id,
)
from magebench.analysis.blunder.blunder_eval_common import (
    decision_index as get_decision_index,
)
from magebench.analysis.blunder.blunder_eval_common import (
    snapshot_index as get_snapshot_index,
)
from magebench.analysis.blunder.extract_decisions import extract_decisions
from magebench.game.game_export_types import (
    Action,
    Annotation,
    Decision,
    GameExport,
    Snapshot,
)

_dev_server_port: int | None = None
_dev_server_proc: subprocess.Popen | None = None


def _find_free_port() -> int:
    """Find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        sockname = s.getsockname()
        assert isinstance(sockname, tuple) and len(sockname) >= 2, f"Unexpected socket name: {sockname!r}"
        port = sockname[1]
        assert isinstance(port, int), f"Expected integer port, got {port!r}"
        return port


def _start_dev_server() -> int:
    """Start the Astro dev server on a free port. Returns the port."""
    global _dev_server_port, _dev_server_proc

    port = _find_free_port()
    website_dir = REPO_ROOT / "website"

    # Generate leaderboard data (required by the game viewer page)
    subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "puppeteer",
            "python",
            "scripts/generate_leaderboard.py",
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        check=True,
    )

    # Install deps quietly, then start dev server
    subprocess.run(
        ["npm", "install", "--prefer-offline", "--no-audit", "--no-fund"],
        cwd=str(website_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )

    _dev_server_proc = subprocess.Popen(
        ["npx", "astro", "dev", "--port", str(port)],
        cwd=str(website_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _dev_server_port = port

    atexit.register(_stop_dev_server)

    # Wait for server to be ready
    for _ in range(30):
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                break
        except OSError:
            time.sleep(0.5)

    print(f"Dev server running at http://localhost:{port}/\n")
    return port


def _stop_dev_server() -> None:
    """Stop the dev server if running."""
    global _dev_server_proc
    if _dev_server_proc is not None:
        _dev_server_proc.terminate()
        _dev_server_proc.wait()
        _dev_server_proc = None


def viewer_url(game_id: str, aftermath_index: int) -> str:
    """Generate a game viewer URL with snapshot parameter."""
    port = _dev_server_port or 4321
    game_id = validate_game_id(game_id)
    return f"http://localhost:{port}/games/{game_id}?s={aftermath_index}"


def _load_game_data(gz_path: str) -> GameExport:
    """Load a game's JSON data from a .json or .json.gz file."""
    return load_game(gz_path)


def _find_decision(decisions: list[Decision], di: int) -> Decision:
    """Find a decision by index. Asserts if not found."""
    for d in decisions:
        if get_decision_index(d) == di:
            return d
    raise AssertionError(f"Decision {di} not found in {len(decisions)} decisions")


def _lookup_existing_annotation(
    decision: Decision,
    game_data: GameExport,
) -> Annotation | None:
    """Look up the annotation from the game file (may be stale). For display only."""
    return lookup_annotation_for_decision(decision, game_data.annotations)


def get_current_annotation(
    decision: Decision,
    game_data: GameExport,
    snapshots: Sequence[Snapshot],
    gz_path: str,
) -> tuple[Annotation | None, int]:
    """Get the current-version annotation for a decision.

    If the game file is at the current BLUNDER_SCRIPT_VERSION, looks up
    the annotation from the game file. Otherwise, runs the annotator on
    this one decision (costs money).

    Call this only after the human gives a verdict.

    Returns (annotation_dict_or_None, annotation_version).
    """
    game_version = game_data.blunder_script_version
    if game_version >= BLUNDER_SCRIPT_VERSION:
        ann = lookup_annotation_for_decision(decision, game_data.annotations)
        return ann, BLUNDER_SCRIPT_VERSION

    # Stale game — run annotator on just this decision
    print(f"  Running annotator (game v{game_version}, current v{BLUNDER_SCRIPT_VERSION})...")
    client, prices = init_api()
    game_ctx = load_game_context(gz_path)

    anns, cost, _parsed_ok, _raw = evaluate_one_decision(
        client,
        OPUS_MODEL,
        prices,
        game_ctx["overview"],
        decision,
        game_ctx["oracle_texts"],
        snapshots,
        game_ctx["actions_by_turn"],
        game_ctx["num_players"],
        game_ctx["all_actions"],
    )
    print(f"  Annotator cost: ${cost:.4f}")
    return (anns[0] if anns else None), BLUNDER_SCRIPT_VERSION


def _recent_actions_before(
    game_actions: Sequence[Action],
    snapshots: Sequence[Snapshot],
    snapshot_index: int,
    count: int = 5,
) -> list[str]:
    """Return the last `count` game action messages before a snapshot's timestamp."""
    if snapshot_index is None or snapshot_index < 0 or snapshot_index >= len(snapshots):
        return []
    snap_ts = snapshots[snapshot_index].ts
    if snap_ts is None:
        return []
    recent: list[str] = []
    for a in game_actions:
        a_ts = a.ts
        if a_ts is None:
            continue
        assert isinstance(a_ts, str), f"action ts must be a string when present, got {a_ts!r}"
        if a_ts > snap_ts:
            break
        msg = a.message
        if msg is None:
            continue
        assert isinstance(msg, str), f"action message must be a string when present, got {msg!r}"
        if msg:
            recent.append(msg)
    return recent[-count:]


def format_play_context(
    game_id: str,
    decision: Decision,
    snapshots: Sequence[Snapshot],
    annotation: Annotation | None,
    game_actions: Sequence[Action] | None = None,
) -> str:
    """Format a decision for display during auditing."""
    aftermath = compute_aftermath_index(decision, snapshots)
    snap_idx = get_snapshot_index(decision)
    snapshot = snapshots[snap_idx] if snap_idx < len(snapshots) else None
    stack = snapshot.stack if snapshot is not None else []
    stack_str = ", ".join(export_record_name(s) for s in stack) if stack else "(empty)"

    # Find the current player's hand
    player_name = decision["player"]
    assert isinstance(player_name, str), f"decision player must be a string, got {player_name!r}"
    hand_str = "?"
    for p in snapshot.players if snapshot is not None else []:
        if p.name == player_name:
            hand = p.hand
            hand_str = ", ".join(export_record_name(h) for h in hand) if hand else "(empty)"
            break

    lines = [
        f"Game: {game_id}",
        f"Player: {decision['player']} | Turn {decision.get('turn', '?')} {decision.get('phase', '?')}",
        f"Stack: {stack_str}",
        f"Hand: {hand_str}",
        f"Message: {decision.get('message', '?')}",
        f"Chosen: {chosen_display(decision)}",
    ]

    # Recent game log actions
    if game_actions is not None:
        snap_idx = get_snapshot_index(decision)
        recent = _recent_actions_before(game_actions, snapshots, snap_idx)
        if recent:
            lines.append("Recent log:")
            lines.extend(f"  {msg}" for msg in recent)

    if annotation:
        prefix = f"Annotator: {annotation.severity} - "
        wrapped = textwrap.fill(
            f'"{annotation.description}"',
            width=120,
            initial_indent=prefix,
            subsequent_indent=" " * len(prefix),
        )
        lines.append(wrapped)
    lines.append(f"Viewer: {viewer_url(game_id, aftermath)}")
    return "\n".join(lines)


_SKIP_GAME = "skip_game"


def collect_verdict() -> tuple[str | None, str | None]:
    """Prompt for human verdict.

    Returns (verdict, notes). Verdict is "blunder", "not_blunder",
    "questionable", None (skip), or _SKIP_GAME. Raises SystemExit on quit.
    """
    while True:
        try:
            resp = (
                input("\nVerdict [b]lunder / [n]ot_blunder / [?] questionable / [s]kip / [g] next game / [q]uit: ")
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(0) from None

        if resp in ("q", "quit"):
            raise SystemExit(0)
        if resp in ("s", "skip"):
            return None, None
        if resp in ("g", "next", "next game"):
            return _SKIP_GAME, None
        if resp in ("b", "blunder"):
            verdict = "blunder"
        elif resp in ("n", "not_blunder", "not"):
            verdict = "not_blunder"
        elif resp in ("?", "questionable"):
            verdict = "questionable"
        else:
            print("  Invalid input. Use b/n/?/s/g/q.")
            continue

        try:
            notes = input("Notes (Enter=skip): ").strip() or None
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(0) from None

        return verdict, notes


def audit_plays(game_filter: str | None = None) -> None:
    """Main audit loop."""
    all_gt = load_ground_truth()
    if not all_gt:
        print("No ground truth entries found. Run 'make blunder-seed' first.")
        return

    # Collect all unaudited entries across games, newest game first
    unaudited: list[tuple[str, dict]] = []
    for game_id, entries in sorted(all_gt.items(), reverse=True):
        if game_filter and game_filter != game_id:
            continue
        unaudited.extend((game_id, entry) for entry in entries if entry.get("verdict") is None)

    if not unaudited:
        total = sum(len(entries) for entries in all_gt.values())
        print(f"All {total} entries already audited!")
        return

    _start_dev_server()
    print(f"{len(unaudited)} unaudited plays to review.\n")

    # Cache game data to avoid re-loading per entry
    game_data_cache: dict[str, GameExport] = {}
    decisions_cache: dict[str, list[Decision]] = {}

    audited_count = 0
    skip_game_id: str | None = None
    for i, (game_id, entry) in enumerate(unaudited):
        if skip_game_id is not None:
            if game_id == skip_game_id:
                continue
            skip_game_id = None
        print(f"--- Play {i + 1}/{len(unaudited)} ---")

        # Load game data (cached)
        if game_id not in game_data_cache:
            gz_path = str(game_path_for_id(game_id))
            game_data_cache[game_id] = _load_game_data(gz_path)
            decisions_cache[game_id] = extract_decisions(gz_path)

        game_data = game_data_cache[game_id]
        decisions = decisions_cache[game_id]
        snapshots = game_data.snapshots
        gz_path = str(game_path_for_id(game_id))

        # Find the decision
        di = entry["decision_index"]
        assert isinstance(di, int), f"decision_index must be an int, got {di!r}"
        decision = _find_decision(decisions, di)

        # Show existing annotation for context (may be stale)
        display_annotation = _lookup_existing_annotation(decision, game_data)
        game_actions = game_data.actions
        print(format_play_context(game_id, decision, snapshots, display_annotation, game_actions))

        verdict, notes = collect_verdict()
        if verdict is None:
            print("  Skipped.\n")
            continue
        if verdict == _SKIP_GAME:
            skipped = sum(1 for gid, _ in unaudited[i:] if gid == game_id)
            print(f"  Skipping remaining {skipped} plays in {game_id}.\n")
            skip_game_id = game_id
            continue

        # Get current-version annotation (re-runs annotator if game is stale)
        annotation, ann_version = get_current_annotation(decision, game_data, snapshots, gz_path)

        # Build and save full audited entry
        audited_entry = make_audited_entry(
            decision_index=di,
            annotation_version=ann_version,
            annotation_severity=annotation.severity if annotation is not None else None,
            annotation_description=annotation.description if annotation is not None else None,
            verdict=verdict,
            human_notes=notes,
        )

        # Replace in-place and save
        game_entries = load_game_ground_truth(game_id)
        for idx, e in enumerate(game_entries):
            if e["decision_index"] == di:
                game_entries[idx] = audited_entry
                break
        save_game_ground_truth(game_id, game_entries)

        audited_count += 1
        print(f"  Saved: {verdict}\n")

    print(f"\nAudited {audited_count} plays.")


def parse_viewer_url(url: str) -> tuple[str, int]:
    """Parse a game viewer URL into (game_id, snapshot_index).

    Accepts:
      http://localhost:4321/games/game_20260214_185313_g1?s=77
      game_20260214_185313_g1?s=77
      /games/game_20260214_185313_g1?s=77
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    parts = [part for part in path.split("/") if part]

    if not parts:
        raise AssertionError(f"Could not extract game_id from URL: {url}")
    if len(parts) == 1:
        raw_game_id = parts[0]
    else:
        assert len(parts) == 2 and parts[0] == "games", f"Invalid viewer path: {url}"
        raw_game_id = parts[1]

    game_id = validate_game_id(raw_game_id)

    # Extract snapshot from query params
    qs = parse_qs(parsed.query)
    s_values = qs.get("s")
    if s_values is None:
        s_values = []
    assert s_values, f"URL must have ?s=N parameter: {url}"
    snapshot = int(s_values[0])

    return game_id, snapshot


def add_from_url(url: str) -> None:
    """Manually add a play from a game viewer URL."""
    _start_dev_server()
    game_id, snapshot = parse_viewer_url(url)

    gz_path = str(game_path_for_id(game_id))
    game_data = _load_game_data(gz_path)

    snapshots = game_data.snapshots
    assert 0 <= snapshot < len(snapshots), f"Snapshot {snapshot} out of range [0, {len(snapshots)})"

    decisions = extract_decisions(gz_path)
    assert decisions, f"No decisions found in {game_id}"

    # Find the decision closest to this snapshot
    best_decision = None
    best_dist = float("inf")

    for d in decisions:
        aftermath = compute_aftermath_index(d, snapshots)
        if aftermath == snapshot:
            best_decision = d
            break
        if get_snapshot_index(d) <= snapshot:
            dist = snapshot - get_snapshot_index(d)
            if dist < best_dist:
                best_dist = dist
                best_decision = d

    assert best_decision is not None, f"No decision found near snapshot {snapshot} in {game_id}"

    # Check if already in ground truth
    existing = load_game_ground_truth(game_id)
    best_di = get_decision_index(best_decision)
    for e in existing:
        if e["decision_index"] == best_di:
            print(f"Decision {best_di} already in ground truth (verdict={e.get('verdict')})")
            return

    # Show existing annotation for context (may be stale)
    display_annotation = _lookup_existing_annotation(best_decision, game_data)
    game_actions = game_data.actions
    print(format_play_context(game_id, best_decision, snapshots, display_annotation, game_actions))

    try:
        notes = input("\nNotes (Enter=skip): ").strip() or None
    except (EOFError, KeyboardInterrupt):
        print()
        notes = None

    # Get current-version annotation (re-runs annotator if game is stale)
    annotation, ann_version = get_current_annotation(best_decision, game_data, snapshots, gz_path)
    audited_entry = make_audited_entry(
        decision_index=best_di,
        annotation_version=ann_version,
        annotation_severity=annotation.severity if annotation is not None else None,
        annotation_description=annotation.description if annotation is not None else None,
        verdict="blunder",
        human_notes=notes,
    )

    save_game_ground_truth(game_id, [*existing, audited_entry])
    print(f"\nAdded as blunder (decision {best_di})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Blunder ground truth audit")
    subparsers = parser.add_subparsers(dest="command")

    audit_parser = subparsers.add_parser("audit", help="Audit unaudited plays")
    audit_parser.add_argument("--game", help="Filter to a specific game ID")

    add_parser = subparsers.add_parser("add", help="Add a play from a viewer URL")
    add_parser.add_argument("url", help="Game viewer URL with ?s=N parameter")

    args = parser.parse_args()

    if args.command == "add":
        add_from_url(args.url)
    else:
        # Default to audit
        game_filter = getattr(args, "game", None)
        audit_plays(game_filter)


if __name__ == "__main__":
    main()
