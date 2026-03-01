#!/usr/bin/env python3
"""Web UI for auditing blunder ground truth entries.

Serves a single-page app with an embedded game board renderer and
JSON API endpoints for listing plays, viewing details, and submitting verdicts.

Usage:
    uv run --project puppeteer python scripts/analysis/blunder_audit_web.py [--port PORT]
    make blunder-audit-web
"""

import argparse
import json
import mimetypes
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from blunder_eval_common import (
    REPO_ROOT,
    chosen_display,
    compute_aftermath_index,
    decision_index as get_decision_index,
    game_path_for_id,
    load_game,
    load_game_ground_truth,
    load_ground_truth,
    lookup_annotation_for_decision,
    make_audited_entry,
    save_game_ground_truth,
    snapshot_index as get_snapshot_index,
)
from extract_decisions import extract_decisions

SCRIPTS_DIR = Path(__file__).resolve().parent
WEBSITE_PUBLIC = REPO_ROOT / "website" / "public"
CONFIG_PATH = Path.home() / ".mage-bench" / "config.json"

# Files we serve from website/public/
STATIC_FILES: dict[str, Path] = {
    "/game-renderer.js": WEBSITE_PUBLIC / "game-renderer.js",
    "/game-renderer.css": WEBSITE_PUBLIC / "game-renderer.css",
    "/game-viewer.js": WEBSITE_PUBLIC / "game-viewer.js",
    "/game-viewer.css": WEBSITE_PUBLIC / "game-viewer.css",
    "/cardback.jpg": WEBSITE_PUBLIC / "cardback.jpg",
}
GAMES_DIR = WEBSITE_PUBLIC / "games"

# In-memory caches (single-user tool, no concurrency concerns)
_game_data_cache: dict[str, dict] = {}
_decisions_cache: dict[str, list[dict]] = {}


def _load_config() -> dict:
    """Load ~/.mage-bench/config.json if it exists."""
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def _get_hostname() -> str:
    """Get configured hostname, defaulting to 'localhost'."""
    return _load_config().get("hostname", "localhost")


def _load_game_cached(game_id: str) -> dict:
    """Load game data with caching."""
    if game_id not in _game_data_cache:
        gz_path = str(game_path_for_id(game_id))
        _game_data_cache[game_id] = load_game(gz_path)
    return _game_data_cache[game_id]


def _load_decisions_cached(game_id: str) -> list[dict]:
    """Load decisions with caching."""
    if game_id not in _decisions_cache:
        gz_path = str(game_path_for_id(game_id))
        _decisions_cache[game_id] = extract_decisions(gz_path)
    return _decisions_cache[game_id]


def _find_decision(decisions: list[dict], di: int) -> dict:
    """Find a decision by index."""
    for d in decisions:
        if get_decision_index(d) == di:
            return d
    raise AssertionError(f"Decision {di} not found in {len(decisions)} decisions")


def _recent_actions_before(
    game_actions: list[dict], snapshots: list[dict], snapshot_idx: int, count: int = 5
) -> list[str]:
    """Return the last `count` game action messages before a snapshot's timestamp."""
    if snapshot_idx is None or snapshot_idx < 0 or snapshot_idx >= len(snapshots):
        return []
    snap_ts = snapshots[snapshot_idx].get("ts", "")
    if not snap_ts:
        return []
    recent: list[str] = []
    for a in game_actions:
        a_ts = a.get("ts", "")
        if a_ts > snap_ts:
            break
        msg = a.get("message", "")
        if msg:
            recent.append(msg)
    return recent[-count:]


def _build_play_summary(game_id: str, entry: dict) -> dict:
    """Build a lightweight play summary from ground truth only (no game file I/O)."""
    return {
        "game_id": game_id,
        "decision_index": entry["decision_index"],
        "verdict": entry.get("verdict"),
        "human_notes": entry.get("human_notes"),
        "annotation_severity": entry.get("annotation_severity"),
    }


def _build_play_detail(game_id: str, di: int) -> dict:
    """Build full play detail including snapshot for board rendering."""
    game_data = _load_game_cached(game_id)
    decisions = _load_decisions_cached(game_id)
    decision = _find_decision(decisions, di)
    snapshots = game_data.get("snapshots", [])

    aftermath_idx = compute_aftermath_index(decision, snapshots)
    snap_idx = get_snapshot_index(decision)

    # Get the "before" snapshot for hand context
    before_snapshot = snapshots[snap_idx] if snap_idx < len(snapshots) else {}

    # Look up annotation
    annotation = lookup_annotation_for_decision(
        decision, game_data.get("annotations", []), snapshots
    )

    # Recent actions
    game_actions = game_data.get("actions", [])
    recent = _recent_actions_before(game_actions, snapshots, snap_idx)

    # Get hand from before-snapshot
    player_name = decision.get("player", "")
    hand_str = "?"
    for p in before_snapshot.get("players", []):
        if p.get("name") == player_name:
            hand = p.get("hand", [])
            hand_str = (
                ", ".join(h if isinstance(h, str) else h.get("name", "?") for h in hand)
                if hand
                else "(empty)"
            )
            break

    # Get ground truth entry
    gt_entries = load_game_ground_truth(game_id)
    gt_entry = None
    for e in gt_entries:
        if e["decision_index"] == di:
            gt_entry = e
            break

    return {
        "game_id": game_id,
        "decision_index": di,
        "player": decision.get("player", "?"),
        "turn": decision.get("turn", "?"),
        "phase": decision.get("phase", "?"),
        "message": decision.get("message", "?"),
        "chosen": chosen_display(decision),
        "hand": hand_str,
        "recent_actions": recent,
        "annotation": {
            "severity": annotation.get("severity") if annotation else None,
            "description": annotation.get("description") if annotation else None,
            "actionTaken": annotation.get("actionTaken") if annotation else None,
            "betterLine": annotation.get("betterLine") if annotation else None,
        },
        "verdict": gt_entry.get("verdict") if gt_entry else None,
        "human_notes": gt_entry.get("human_notes") if gt_entry else None,
        "aftermath_index": aftermath_idx,
        "snapshot_index": snap_idx,
    }


def _handle_verdict(game_id: str, di: int, body: dict) -> dict:
    """Process a verdict submission."""
    from blunder_audit import _get_current_annotation

    verdict = body["verdict"]
    assert verdict in ("blunder", "not_blunder", "questionable"), (
        f"Invalid verdict: {verdict}"
    )
    notes = body.get("notes") or None

    game_data = _load_game_cached(game_id)
    decisions = _load_decisions_cached(game_id)
    decision = _find_decision(decisions, di)
    snapshots = game_data.get("snapshots", [])
    gz_path = str(game_path_for_id(game_id))

    annotation, ann_version = _get_current_annotation(
        decision, game_data, snapshots, gz_path
    )

    audited_entry = make_audited_entry(
        decision_index=di,
        annotation_version=ann_version,
        annotation_severity=annotation.get("severity") if annotation else None,
        annotation_description=annotation.get("description") if annotation else None,
        verdict=verdict,
        human_notes=notes,
    )

    # Replace in-place and save
    game_entries = load_game_ground_truth(game_id)
    replaced = False
    for idx, e in enumerate(game_entries):
        if e["decision_index"] == di:
            game_entries[idx] = audited_entry
            replaced = True
            break
    if not replaced:
        game_entries.append(audited_entry)
    save_game_ground_truth(game_id, game_entries)

    return audited_entry


def _compute_stats() -> dict:
    """Compute audit progress stats."""
    all_gt = load_ground_truth()
    total = 0
    audited = 0
    verdicts: dict[str, int] = {}
    for entries in all_gt.values():
        for entry in entries:
            total += 1
            v = entry.get("verdict")
            if v is not None:
                audited += 1
                verdicts[v] = verdicts.get(v, 0) + 1
    return {
        "total": total,
        "audited": audited,
        "unaudited": total - audited,
        "verdicts": verdicts,
        "games": len(all_gt),
    }


class AuditHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the audit web UI."""

    def log_message(self, format: str, *args: object) -> None:
        """Suppress default request logging."""
        pass

    def _send_json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status)

    def _send_file(self, path: Path, content_type: str | None = None) -> None:
        if not path.exists():
            self._send_error(404, f"Not found: {path.name}")
            return
        data = path.read_bytes()
        if content_type is None:
            content_type = (
                mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            )
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        # Static files
        if path == "/" or path == "/index.html":
            self._send_file(SCRIPTS_DIR / "audit_ui.html", "text/html")
            return

        if path in STATIC_FILES:
            self._send_file(STATIC_FILES[path])
            return

        # Game data files from website/public/games/
        if path.startswith("/games/"):
            filename = path.split("/games/", 1)[1]
            # Reject path traversal
            if ".." in filename or "/" in filename:
                self._send_error(400, "Invalid path")
                return
            filepath = GAMES_DIR / filename
            self._send_file(filepath)
            return

        # API: list plays
        if path == "/api/plays":
            game_filter = qs.get("game", [None])[0]
            all_gt = load_ground_truth()
            plays = []
            for gid, entries in sorted(all_gt.items(), reverse=True):
                if game_filter and game_filter != gid:
                    continue
                for entry in entries:
                    plays.append(_build_play_summary(gid, entry))
            self._send_json(plays)
            return

        # API: play detail
        if path.startswith("/api/plays/") and path.count("/") == 4:
            parts = path.split("/")
            game_id = parts[3]
            try:
                di = int(parts[4])
            except ValueError:
                self._send_error(400, "Invalid decision index")
                return
            try:
                detail = _build_play_detail(game_id, di)
                self._send_json(detail)
            except Exception as e:
                self._send_error(500, str(e))
            return

        # API: stats
        if path == "/api/stats":
            self._send_json(_compute_stats())
            return

        self._send_error(404, f"Not found: {path}")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        # API: submit verdict
        if path.startswith("/api/plays/") and path.endswith("/verdict"):
            parts = path.split("/")
            # /api/plays/{game_id}/{di}/verdict
            if len(parts) != 6:
                self._send_error(400, "Invalid path")
                return
            game_id = parts[3]
            try:
                di = int(parts[4])
            except ValueError:
                self._send_error(400, "Invalid decision index")
                return

            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length))

            try:
                result = _handle_verdict(game_id, di, body)
                self._send_json(result)
            except Exception as e:
                self._send_error(500, str(e))
            return

        self._send_error(404, f"Not found: {path}")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def _find_free_port() -> int:
    """Find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Blunder audit web UI")
    parser.add_argument("--port", type=int, default=0, help="Port (0 = auto)")
    parser.add_argument("--game", help="Filter to a specific game ID")
    args = parser.parse_args()

    port = args.port or _find_free_port()
    hostname = _get_hostname()

    server = HTTPServer(("0.0.0.0", port), AuditHandler)

    # Pre-load ground truth to report stats at startup
    all_gt = load_ground_truth()
    total = sum(len(entries) for entries in all_gt.values())
    unaudited = sum(
        1 for entries in all_gt.values() for e in entries if e.get("verdict") is None
    )

    url = f"http://{hostname}:{port}/"
    if args.game:
        url += f"?game={args.game}"

    print(f"Blunder audit UI: {url}")
    print(f"{unaudited}/{total} plays unaudited across {len(all_gt)} games")
    print("Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
