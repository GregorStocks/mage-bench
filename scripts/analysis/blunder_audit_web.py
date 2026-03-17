#!/usr/bin/env python3
"""Web UI for auditing blunder ground truth entries.

Serves a single-page app with an embedded game board renderer and
JSON API endpoints for listing plays, viewing details, and submitting verdicts.

Usage:
    uv run --project puppeteer python scripts/analysis/blunder_audit_web.py [--port PORT] [--bind-host HOST]
    make blunder-audit-web
"""

import argparse
import json
import mimetypes
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from schemas.game_export_types import Action, GameExport, Snapshot
from scripts.analysis.blunder_eval_common import (
    REPO_ROOT,
    chosen_display,
    compute_aftermath_index,
    game_path_for_id,
    is_cast_rolled_back,
    is_forced,
    is_mana_ability_subdecision,
    is_rolled_back,
    load_game,
    load_game_ground_truth,
    load_ground_truth,
    lookup_annotation_for_decision,
    make_audited_entry,
    save_game_ground_truth,
    validate_export_filename,
    validate_game_id,
)
from scripts.analysis.blunder_eval_common import (
    decision_index as get_decision_index,
)
from scripts.analysis.blunder_eval_common import (
    snapshot_index as get_snapshot_index,
)
from scripts.analysis.extract_decisions import extract_decisions

SCRIPTS_DIR = Path(__file__).resolve().parent
WEBSITE_PUBLIC = REPO_ROOT / "website" / "public"
WEBSITE_SCRIPTS = REPO_ROOT / "website" / "src" / "scripts"
WEBSITE_STYLES = REPO_ROOT / "website" / "src" / "styles"
CONFIG_PATH = Path.home() / ".mage-bench" / "config.json"

# Remote audit sessions often run on a dev server and are browsed from a second
# machine, so wildcard binding remains the default. Use --bind-host 127.0.0.1
# for local-only access.
DEFAULT_BIND_HOST = "0.0.0.0"

# Files the standalone audit UI serves directly from the website sources.
STATIC_FILES: dict[str, Path] = {
    "/game-renderer.js": WEBSITE_SCRIPTS / "game-renderer.js",
    "/game-renderer.css": WEBSITE_STYLES / "game-renderer.css",
    "/game-viewer.js": WEBSITE_SCRIPTS / "game-viewer.js",
    "/game-viewer.css": WEBSITE_STYLES / "game-viewer.css",
    "/cardback.jpg": WEBSITE_PUBLIC / "cardback.jpg",
}
GAMES_DIR = WEBSITE_PUBLIC / "games"

# In-memory caches (single-user tool, no concurrency concerns)
_game_data_cache: dict[str, GameExport] = {}
_decisions_cache: dict[str, list[dict[str, object]]] = {}


class AuditApiError(RuntimeError):
    """Expected request/data error that should become a JSON 500."""


def _load_config() -> dict:
    """Load ~/.mage-bench/config.json if it exists."""
    if CONFIG_PATH.exists():
        data = json.loads(CONFIG_PATH.read_text())
        assert isinstance(data, dict), f"{CONFIG_PATH}: expected JSON object"
        return data
    return {}


def _get_hostname() -> str:
    """Get configured hostname, defaulting to 'localhost'."""
    hostname = _load_config().get("hostname", "localhost")
    assert isinstance(hostname, str), f"hostname must be a string, got {hostname!r}"
    return hostname or "localhost"


def _format_url_host(host: str) -> str:
    """Format a host for use in an HTTP URL."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _viewer_url(bind_host: str, port: int, game_id: str | None = None) -> str:
    """Return the URL users should browse to for the current binding."""
    browse_host = _get_hostname() if bind_host == DEFAULT_BIND_HOST else bind_host
    url = f"http://{_format_url_host(browse_host)}:{port}/"
    if game_id:
        url += f"?game={game_id}"
    return url


def _load_game_cached(game_id: str) -> GameExport:
    """Load game data with caching."""
    if game_id not in _game_data_cache:
        try:
            gz_path = str(game_path_for_id(game_id))
            _game_data_cache[game_id] = load_game(gz_path)
        except (
            AssertionError,
            FileNotFoundError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            raise AuditApiError(str(exc)) from exc
    return _game_data_cache[game_id]


def _load_decisions_cached(game_id: str) -> list[dict[str, object]]:
    """Load decisions with caching."""
    if game_id not in _decisions_cache:
        try:
            gz_path = str(game_path_for_id(game_id))
            _decisions_cache[game_id] = extract_decisions(gz_path)
        except (
            AssertionError,
            FileNotFoundError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            raise AuditApiError(str(exc)) from exc
    return _decisions_cache[game_id]


def _find_decision(decisions: list[dict[str, object]], di: int) -> dict[str, object]:
    """Find a decision by index."""
    for d in decisions:
        if get_decision_index(d) == di:
            return d
    raise AuditApiError(f"Decision {di} not found in {len(decisions)} decisions")


def _recent_actions_before(
    game_actions: list[Action],
    snapshots: list[Snapshot],
    snapshot_idx: int,
    count: int = 5,
) -> list[str]:
    """Return the last `count` game action messages before a snapshot's timestamp."""
    if snapshot_idx is None or snapshot_idx < 0 or snapshot_idx >= len(snapshots):
        return []
    snap_ts = snapshots[snapshot_idx].get("ts")
    if snap_ts is None:
        return []
    recent: list[str] = []
    for a in game_actions:
        a_ts = a.get("ts", "")
        assert isinstance(a_ts, str), (
            f"action ts must be a string when present, got {a_ts!r}"
        )
        if a_ts > snap_ts:
            break
        msg = a.get("message", "")
        assert isinstance(msg, str), (
            f"action message must be a string when present, got {msg!r}"
        )
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
    snapshots = game_data["snapshots"]

    aftermath_idx = compute_aftermath_index(decision, snapshots)
    snap_idx = get_snapshot_index(decision)

    # Get the "before" snapshot for hand context
    before_snapshot = snapshots[snap_idx] if snap_idx < len(snapshots) else None

    # Look up annotation
    annotation = lookup_annotation_for_decision(decision, game_data["annotations"])

    # Recent actions
    game_actions = game_data["actions"]
    recent = _recent_actions_before(game_actions, snapshots, snap_idx)

    # Get hand from before-snapshot
    player_name = decision.get("player", "")
    hand_str = "?"
    for p in before_snapshot["players"] if before_snapshot is not None else []:
        if p.get("name") == player_name:
            hand = p["hand"]
            hand_str = (
                ", ".join(
                    h
                    if isinstance(h, str)
                    else h.get("name", "?")
                    if isinstance(h, dict)
                    else str(h)
                    for h in hand
                )
                if hand
                else "(empty)"
            )
            break

    # Get ground truth entry
    try:
        gt_entries = load_game_ground_truth(game_id)
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditApiError(str(exc)) from exc
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
    from scripts.analysis.blunder_audit import _get_current_annotation

    if not isinstance(body, dict):
        raise AuditApiError("Expected JSON object body")

    try:
        verdict = body["verdict"]
    except KeyError as exc:
        raise AuditApiError("Missing verdict") from exc
    if verdict not in ("blunder", "not_blunder", "questionable"):
        raise AuditApiError(f"Invalid verdict: {verdict}")
    notes = body.get("notes") or None

    game_data = _load_game_cached(game_id)
    decisions = _load_decisions_cached(game_id)
    decision = _find_decision(decisions, di)
    snapshots = game_data["snapshots"]
    gz_path = str(game_path_for_id(game_id))

    try:
        annotation, ann_version = _get_current_annotation(
            decision, game_data, snapshots, gz_path
        )
    except (AssertionError, FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise AuditApiError(str(exc)) from exc

    annotation_severity = None
    annotation_description = None
    if annotation is not None:
        severity = annotation.get("severity")
        description = annotation.get("description")
        assert isinstance(severity, str), (
            f"annotation severity must be a string, got {severity!r}"
        )
        assert isinstance(description, str), (
            f"annotation description must be a string, got {description!r}"
        )
        annotation_severity = severity
        annotation_description = description
    audited_entry = make_audited_entry(
        decision_index=di,
        annotation_version=ann_version,
        annotation_severity=annotation_severity,
        annotation_description=annotation_description,
        verdict=verdict,
        human_notes=notes,
    )

    # Replace in-place and save
    try:
        game_entries = load_game_ground_truth(game_id)
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditApiError(str(exc)) from exc
    replaced = False
    for idx, e in enumerate(game_entries):
        if e["decision_index"] == di:
            game_entries[idx] = audited_entry
            replaced = True
            break
    if not replaced:
        game_entries.append(audited_entry)
    try:
        save_game_ground_truth(game_id, game_entries)
    except OSError as exc:
        raise AuditApiError(str(exc)) from exc

    return audited_entry


def _find_decisions_at_snapshot(game_id: str, snap_idx: int) -> list[dict]:
    """Find all interesting decisions at a given snapshot index.

    Checks both snapshot_index (before the decision) and aftermath_index
    (after the decision resolved). Excludes forced, rolled-back, and
    mana-ability sub-decisions.
    """
    decisions = _load_decisions_cached(game_id)
    game_data = _load_game_cached(game_id)
    snapshots = game_data.get("snapshots", [])

    results = []
    seen_di: set[int] = set()

    for d in decisions:
        if is_forced(d) or is_rolled_back(d) or is_cast_rolled_back(d):
            continue
        if is_mana_ability_subdecision(d):
            continue

        di = get_decision_index(d)
        s_idx = get_snapshot_index(d)
        a_idx = compute_aftermath_index(d, snapshots)

        if (s_idx == snap_idx or a_idx == snap_idx) and di not in seen_di:
            seen_di.add(di)
            results.append(
                {
                    "decision_index": di,
                    "player": d.get("player", "?"),
                    "turn": d.get("turn", "?"),
                    "phase": d.get("phase", "?"),
                    "message": d.get("message", "?"),
                    "chosen": chosen_display(d),
                    "snapshot_index": s_idx,
                    "aftermath_index": a_idx,
                }
            )

    return results


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

    def log_message(self, fmt: str, *args: object) -> None:
        """Suppress default request logging."""

    def _send_json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status)

    def _parse_game_id(self, game_id: str) -> str | None:
        try:
            return validate_game_id(game_id)
        except AssertionError as exc:
            self._send_error(400, str(exc))
            return None

    def _parse_export_filename(self, filename: str) -> str | None:
        try:
            return validate_export_filename(filename)
        except AssertionError as exc:
            self._send_error(400, str(exc))
            return None

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
            filename = self._parse_export_filename(path.removeprefix("/games/"))
            if filename is None:
                return
            filepath = GAMES_DIR / filename
            self._send_file(filepath, "application/json")
            return

        # API: list plays
        if path == "/api/plays":
            game_filter = qs.get("game", [None])[0]
            if game_filter is not None:
                game_filter = self._parse_game_id(game_filter)
                if game_filter is None:
                    return
            all_gt = load_ground_truth()
            plays: list[dict] = []
            for gid, entries in sorted(all_gt.items(), reverse=True):
                if game_filter and game_filter != gid:
                    continue
                plays.extend(_build_play_summary(gid, entry) for entry in entries)
            self._send_json(plays)
            return

        # API: play detail
        if path.startswith("/api/plays/") and path.count("/") == 4:
            parts = path.split("/")
            game_id = self._parse_game_id(parts[3])
            if game_id is None:
                return
            try:
                di = int(parts[4])
            except ValueError:
                self._send_error(400, "Invalid decision index")
                return
            try:
                detail = _build_play_detail(game_id, di)
                self._send_json(detail)
            except AuditApiError as e:
                self._send_error(500, str(e))
            return

        # API: decisions at a snapshot index
        if path.startswith("/api/decisions-at-snapshot/"):
            parts = path.split("/")
            if len(parts) != 5:
                self._send_error(
                    400,
                    "Expected /api/decisions-at-snapshot/{game_id}/{snapshot_index}",
                )
                return
            game_id = self._parse_game_id(parts[3])
            if game_id is None:
                return
            try:
                snap_idx = int(parts[4])
            except ValueError:
                self._send_error(400, "Invalid snapshot index")
                return
            try:
                results = _find_decisions_at_snapshot(game_id, snap_idx)
                self._send_json(results)
            except AuditApiError as e:
                self._send_error(500, str(e))
            return

        # Handle stats endpoint
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
            game_id = self._parse_game_id(parts[3])
            if game_id is None:
                return
            try:
                di = int(parts[4])
            except ValueError:
                self._send_error(400, "Invalid decision index")
                return

            content_length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(content_length))
            except json.JSONDecodeError as exc:
                self._send_error(400, f"Invalid JSON: {exc.msg}")
                return

            try:
                result = _handle_verdict(game_id, di, body)
                self._send_json(result)
            except AuditApiError as e:
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
        sockname = s.getsockname()
        assert isinstance(sockname, tuple) and len(sockname) >= 2, (
            f"Unexpected socket name: {sockname!r}"
        )
        port = sockname[1]
        assert isinstance(port, int), f"Expected integer port, got {port!r}"
        return port


def main() -> None:
    parser = argparse.ArgumentParser(description="Blunder audit web UI")
    parser.add_argument("--port", type=int, default=0, help="Port (0 = auto)")
    parser.add_argument(
        "--bind-host",
        default=DEFAULT_BIND_HOST,
        help=(
            "Host/interface to bind "
            f"(default: {DEFAULT_BIND_HOST} for remote-access dev servers; "
            "use 127.0.0.1 for local-only access)"
        ),
    )
    parser.add_argument("--game", help="Filter to a specific game ID")
    args = parser.parse_args()

    port = args.port or _find_free_port()

    server = HTTPServer((args.bind_host, port), AuditHandler)

    # Pre-load ground truth to report stats at startup
    all_gt = load_ground_truth()
    total = sum(len(entries) for entries in all_gt.values())
    unaudited = sum(
        1 for entries in all_gt.values() for e in entries if e.get("verdict") is None
    )

    url = _viewer_url(args.bind_host, port, args.game)
    print(f"Listening on {args.bind_host}:{port}")
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
