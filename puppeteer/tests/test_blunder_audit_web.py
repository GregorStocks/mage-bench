"""Tests for the blunder audit web UI server."""

import json
import threading
import time
from http.server import HTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

import scripts.analysis.blunder_audit_web as blunder_audit_web


@pytest.fixture()
def _temp_ground_truth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up temp ground truth directory with sample data."""
    gt_dir = tmp_path / "ground_truth"
    gt_dir.mkdir()
    monkeypatch.setattr("scripts.analysis.blunder_eval_common.GROUND_TRUTH_DIR", gt_dir)

    # Write a sample ground truth file
    data = [
        {"decision_index": 0},
        {"decision_index": 5, "verdict": "blunder", "human_notes": "obvious"},
    ]
    (gt_dir / "game_test_001.json").write_text(json.dumps(data))
    return gt_dir


@pytest.fixture()
def _temp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up temp config with custom hostname."""
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"hostname": "my-server.local"}))
    monkeypatch.setattr("scripts.analysis.blunder_audit_web.CONFIG_PATH", config_path)
    return config_path


@pytest.fixture()
def server_port(_temp_ground_truth: Path) -> int:
    """Start the audit web server on a free port and return the port."""
    handler = blunder_audit_web.AuditHandler
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    # Wait for server to be ready
    for _ in range(20):
        try:
            urlopen(f"http://127.0.0.1:{port}/api/stats", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    yield port
    httpd.shutdown()


def _get(port: int, path: str) -> dict:
    """Make a GET request and return parsed JSON."""
    resp = urlopen(f"http://127.0.0.1:{port}{path}", timeout=5)
    return json.loads(resp.read())


class TestStatsEndpoint:
    def test_returns_counts(self, server_port: int) -> None:
        stats = _get(server_port, "/api/stats")
        assert stats["total"] == 2
        assert stats["audited"] == 1
        assert stats["unaudited"] == 1
        assert stats["verdicts"]["blunder"] == 1

    def test_games_count(self, server_port: int) -> None:
        stats = _get(server_port, "/api/stats")
        assert stats["games"] == 1


class TestPlaysEndpoint:
    def test_lists_all_plays(self, server_port: int) -> None:
        plays = _get(server_port, "/api/plays")
        assert len(plays) == 2
        # Should be sorted newest game first, but we only have one game
        game_ids = {p["game_id"] for p in plays}
        assert game_ids == {"game_test_001"}

    def test_includes_verdict(self, server_port: int) -> None:
        plays = _get(server_port, "/api/plays")
        by_di = {p["decision_index"]: p for p in plays}
        assert by_di[0]["verdict"] is None
        assert by_di[5]["verdict"] == "blunder"


class TestStaticFiles:
    def test_serves_html(self, server_port: int) -> None:
        resp = urlopen(f"http://127.0.0.1:{server_port}/", timeout=5)
        assert resp.status == 200
        content_type = resp.headers.get("Content-Type", "")
        assert "text/html" in content_type

    def test_serves_game_renderer_js(self, server_port: int) -> None:
        resp = urlopen(f"http://127.0.0.1:{server_port}/game-renderer.js", timeout=5)
        assert resp.status == 200
        body = resp.read().decode()
        assert "GameRenderer" in body

    def test_serves_game_renderer_css(self, server_port: int) -> None:
        resp = urlopen(f"http://127.0.0.1:{server_port}/game-renderer.css", timeout=5)
        assert resp.status == 200

    def test_serves_game_viewer_js(self, server_port: int) -> None:
        resp = urlopen(f"http://127.0.0.1:{server_port}/game-viewer.js", timeout=5)
        assert resp.status == 200
        body = resp.read().decode()
        assert "GameViewer" in body

    def test_serves_game_viewer_css(self, server_port: int) -> None:
        resp = urlopen(f"http://127.0.0.1:{server_port}/game-viewer.css", timeout=5)
        assert resp.status == 200


class TestHostnameConfig:
    def test_reads_hostname(self, _temp_config: Path, _temp_ground_truth: Path) -> None:
        assert blunder_audit_web._get_hostname() == "my-server.local"

    def test_defaults_to_localhost(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(blunder_audit_web, "CONFIG_PATH", Path("/nonexistent/config.json"))
        assert blunder_audit_web._get_hostname() == "localhost"


class TestNotFound:
    def test_unknown_path_returns_404(self, server_port: int) -> None:
        req = Request(f"http://127.0.0.1:{server_port}/api/nonexistent")
        try:
            urlopen(req, timeout=5)
            pytest.fail("Expected 404")
        except Exception as e:
            assert "404" in str(e)
