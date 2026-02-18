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
import gzip
import json
import socket
import textwrap
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from blunder_eval_common import (
    REPO_ROOT,
    compute_aftermath_index,
    game_path_for_id,
    load_game_ground_truth,
    load_ground_truth,
    make_ground_truth_entry,
    save_game_ground_truth,
)
from extract_decisions import extract_decisions

_dev_server_port: int | None = None
_dev_server_proc: subprocess.Popen | None = None


def _find_free_port() -> int:
    """Find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


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
    return f"http://localhost:{port}/games/{game_id}?s={aftermath_index}"


def format_play_context(game_id: str, entry: dict) -> str:
    """Format a ground truth entry for display during auditing."""
    lines = [
        f"Game: {game_id}",
        f"Player: {entry['player']} | Turn {entry.get('turn', '?')} {entry.get('phase', '?')}",
        f"Message: {entry.get('message', '?')}",
        f"Chosen: {entry.get('chosen_display', '?')}",
    ]
    sev = entry.get("annotation_severity")
    desc = entry.get("annotation_description")
    if sev and desc:
        prefix = f"Annotator: {sev} - "
        wrapped = textwrap.fill(
            f'"{desc}"',
            width=80,
            initial_indent=prefix,
            subsequent_indent=" " * len(prefix),
        )
        lines.append(wrapped)
    lines.append(f"Viewer: {viewer_url(game_id, entry.get('aftermath_index', 0))}")
    return "\n".join(lines)


def collect_verdict() -> tuple[str | None, str | None]:
    """Prompt for human verdict.

    Returns (verdict, notes). Verdict is "blunder", "not_blunder", or None (skip).
    Raises SystemExit on quit.
    """
    while True:
        try:
            resp = (
                input("\nVerdict [b]lunder / [n]ot_blunder / [s]kip / [q]uit: ")
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(0)

        if resp in ("q", "quit"):
            raise SystemExit(0)
        if resp in ("s", "skip"):
            return None, None
        if resp in ("b", "blunder"):
            verdict = "blunder"
        elif resp in ("n", "not_blunder", "not"):
            verdict = "not_blunder"
        else:
            print("  Invalid input. Use b/n/s/q.")
            continue

        try:
            notes = input("Notes (Enter=skip): ").strip() or None
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(0)

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
        for entry in entries:
            if entry.get("verdict") is None:
                unaudited.append((game_id, entry))

    if not unaudited:
        total = sum(len(entries) for entries in all_gt.values())
        print(f"All {total} entries already audited!")
        return

    _start_dev_server()
    print(f"{len(unaudited)} unaudited plays to review.\n")

    audited_count = 0
    for i, (game_id, entry) in enumerate(unaudited):
        print(f"--- Play {i + 1}/{len(unaudited)} ---")
        print(format_play_context(game_id, entry))

        verdict, notes = collect_verdict()
        if verdict is None:
            print("  Skipped.\n")
            continue

        entry["verdict"] = verdict
        entry["human_notes"] = notes
        entry["audited_at"] = datetime.now(timezone.utc).isoformat()

        # Save immediately (crash-safe)
        game_entries = load_game_ground_truth(game_id)
        for e in game_entries:
            if e["decision_index"] == entry["decision_index"]:
                e["verdict"] = verdict
                e["human_notes"] = notes
                e["audited_at"] = entry["audited_at"]
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

    # Extract game_id from path
    # Handle /games/{game_id} or just {game_id}
    if "/games/" in path:
        game_id = path.split("/games/")[-1]
    else:
        game_id = path.lstrip("/")

    assert game_id, f"Could not extract game_id from URL: {url}"

    # Extract snapshot from query params
    qs = parse_qs(parsed.query)
    s_values = qs.get("s", [])
    assert s_values, f"URL must have ?s=N parameter: {url}"
    snapshot = int(s_values[0])

    return game_id, snapshot


def add_from_url(url: str) -> None:
    """Manually add a play from a game viewer URL."""
    _start_dev_server()
    game_id, snapshot = parse_viewer_url(url)

    gz_path = str(game_path_for_id(game_id))

    with gzip.open(gz_path, "rt") as f:
        data = json.load(f)

    snapshots = data.get("snapshots", [])
    assert 0 <= snapshot < len(snapshots), (
        f"Snapshot {snapshot} out of range [0, {len(snapshots)})"
    )

    decisions = extract_decisions(gz_path)
    assert decisions, f"No decisions found in {game_id}"

    # Find the decision closest to this snapshot
    # Look for decisions where snapshot_index <= target snapshot,
    # or whose aftermath_index matches
    best_decision = None
    best_dist = float("inf")

    for d in decisions:
        aftermath = compute_aftermath_index(d, snapshots)
        if aftermath == snapshot:
            best_decision = d
            break
        if d["snapshot_index"] <= snapshot:
            dist = snapshot - d["snapshot_index"]
            if dist < best_dist:
                best_dist = dist
                best_decision = d

    assert best_decision is not None, (
        f"No decision found near snapshot {snapshot} in {game_id}"
    )

    # Check if already in ground truth
    existing = load_game_ground_truth(game_id)
    for e in existing:
        if e["decision_index"] == best_decision["decision_index"]:
            print(
                f"Decision {best_decision['decision_index']} already in ground truth "
                f"(verdict={e.get('verdict')})"
            )
            return

    entry = make_ground_truth_entry(best_decision, snapshots, source="manual")
    entry["verdict"] = "blunder"
    entry["audited_at"] = datetime.now(timezone.utc).isoformat()

    # Show what we found
    print(f"Game: {game_id}")
    print(
        f"Decision {best_decision['decision_index']}: {best_decision['player']} | "
        f"Turn {best_decision.get('turn', '?')} {best_decision.get('phase', '?')}"
    )
    print(f"Message: {best_decision.get('message', '?')}")
    print(f"Chosen: {entry['chosen_display']}")
    print(f"Viewer: {viewer_url(game_id, entry['aftermath_index'])}")

    try:
        notes = input("\nNotes (Enter=skip): ").strip() or None
    except (EOFError, KeyboardInterrupt):
        print()
        notes = None

    entry["human_notes"] = notes

    save_game_ground_truth(game_id, existing + [entry])
    print(f"\nAdded as blunder (decision {best_decision['decision_index']})")


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
