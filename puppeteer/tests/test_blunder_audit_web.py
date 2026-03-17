"""Tests for the blunder audit web UI server."""

import json
import threading
import time
from http.server import HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

import scripts.analysis.blunder_audit_web as blunder_audit_web

VALID_GAME_ID = "game_20260214_005111_g1"


@pytest.fixture
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
    (gt_dir / f"{VALID_GAME_ID}.json").write_text(json.dumps(data))
    return gt_dir


@pytest.fixture
def _temp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up temp config with custom hostname."""
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"hostname": "my-server.local"}))
    monkeypatch.setattr("scripts.analysis.blunder_audit_web.CONFIG_PATH", config_path)
    return config_path


@pytest.fixture
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
        except URLError:
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
        assert game_ids == {VALID_GAME_ID}

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


class TestExpectedApiErrors:
    def test_find_decision_raises_audit_api_error(self) -> None:
        with pytest.raises(blunder_audit_web.AuditApiError, match="Decision 9 not found"):
            blunder_audit_web._find_decision([], 9)

    def test_play_detail_returns_json_500(self, server_port: int, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(_game_id: str, _di: int) -> dict:
            raise blunder_audit_web.AuditApiError("detail failed")

        monkeypatch.setattr(blunder_audit_web, "_build_play_detail", _raise)

        with pytest.raises(HTTPError) as excinfo:
            urlopen(f"http://127.0.0.1:{server_port}/api/plays/{VALID_GAME_ID}/0", timeout=5)

        assert excinfo.value.code == 500
        assert json.loads(excinfo.value.read()) == {"error": "detail failed"}

    def test_post_invalid_json_returns_400(self, server_port: int) -> None:
        req = Request(
            f"http://127.0.0.1:{server_port}/api/plays/{VALID_GAME_ID}/5/verdict",
            data=b"{",
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with pytest.raises(HTTPError) as excinfo:
            urlopen(req, timeout=5)

        assert excinfo.value.code == 400
        assert json.loads(excinfo.value.read()) == {
            "error": "Invalid JSON: Expecting property name enclosed in double quotes"
        }

    def test_verdict_returns_json_500(self, server_port: int, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(_game_id: str, _di: int, _body: dict) -> dict:
            raise blunder_audit_web.AuditApiError("save failed")

        monkeypatch.setattr(blunder_audit_web, "_handle_verdict", _raise)
        req = Request(
            f"http://127.0.0.1:{server_port}/api/plays/{VALID_GAME_ID}/5/verdict",
            data=json.dumps({"verdict": "blunder"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with pytest.raises(HTTPError) as excinfo:
            urlopen(req, timeout=5)

        assert excinfo.value.code == 500
        assert json.loads(excinfo.value.read()) == {"error": "save failed"}


class TestNotFound:
    def test_unknown_path_returns_404(self, server_port: int) -> None:
        req = Request(f"http://127.0.0.1:{server_port}/api/nonexistent")
        with pytest.raises(HTTPError, match="404") as excinfo:
            urlopen(req, timeout=5)
        assert excinfo.value.code == 404


class TestPathValidation:
    def test_rejects_invalid_game_filter(self, server_port: int) -> None:
        with pytest.raises(HTTPError) as excinfo:
            urlopen(f"http://127.0.0.1:{server_port}/api/plays?game=../../etc/passwd", timeout=5)

        assert excinfo.value.code == 400
        assert json.loads(excinfo.value.read()) == {"error": "Invalid game_id: '../../etc/passwd'"}

    def test_rejects_invalid_game_export_filename(
        self,
        server_port: int,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(blunder_audit_web, "GAMES_DIR", tmp_path)

        with pytest.raises(HTTPError) as excinfo:
            urlopen(f"http://127.0.0.1:{server_port}/games/{VALID_GAME_ID}/extra", timeout=5)

        assert excinfo.value.code == 400
        assert json.loads(excinfo.value.read()) == {"error": f"Invalid game export filename: '{VALID_GAME_ID}/extra'"}

    def test_serves_valid_game_export_file(
        self,
        server_port: int,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(blunder_audit_web, "GAMES_DIR", tmp_path)
        payload = b'{"id":"game_20260214_005111_g1"}'
        (tmp_path / f"{VALID_GAME_ID}.json").write_bytes(payload)

        resp = urlopen(
            f"http://127.0.0.1:{server_port}/games/{VALID_GAME_ID}.json",
            timeout=5,
        )

        assert resp.status == 200
        assert "application/json" in resp.headers.get("Content-Type", "")
        assert resp.read() == payload


class TestServerBinding:
    def test_main_defaults_to_explicit_all_interfaces_binding(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        captured: dict[str, object] = {}

        class FakeServer:
            def __init__(self, address: tuple[str, int], _handler: type) -> None:
                captured["address"] = address

            def serve_forever(self) -> None:
                raise KeyboardInterrupt

            def shutdown(self) -> None:
                captured["shutdown"] = True

        monkeypatch.setattr(blunder_audit_web, "HTTPServer", FakeServer)
        monkeypatch.setattr(blunder_audit_web, "_find_free_port", lambda: 4567)
        monkeypatch.setattr(blunder_audit_web, "_get_hostname", lambda: "devbox")
        monkeypatch.setattr(blunder_audit_web, "load_ground_truth", dict)
        monkeypatch.setattr("sys.argv", ["blunder_audit_web.py"])

        blunder_audit_web.main()

        output = capsys.readouterr().out
        assert captured["address"] == (blunder_audit_web.DEFAULT_BIND_HOST, 4567)
        assert captured["shutdown"] is True
        assert "Listening on 0.0.0.0:4567" in output
        assert "Blunder audit UI: http://devbox:4567/" in output

    def test_main_allows_bind_host_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        captured: dict[str, object] = {}

        class FakeServer:
            def __init__(self, address: tuple[str, int], _handler: type) -> None:
                captured["address"] = address

            def serve_forever(self) -> None:
                raise KeyboardInterrupt

            def shutdown(self) -> None:
                captured["shutdown"] = True

        monkeypatch.setattr(blunder_audit_web, "HTTPServer", FakeServer)
        monkeypatch.setattr(blunder_audit_web, "_find_free_port", lambda: 4567)
        monkeypatch.setattr(blunder_audit_web, "_get_hostname", lambda: "devbox")
        monkeypatch.setattr(blunder_audit_web, "load_ground_truth", dict)
        monkeypatch.setattr(
            "sys.argv",
            ["blunder_audit_web.py", "--bind-host", "127.0.0.1"],
        )

        blunder_audit_web.main()

        output = capsys.readouterr().out
        assert captured["address"] == ("127.0.0.1", 4567)
        assert captured["shutdown"] is True
        assert "Listening on 127.0.0.1:4567" in output
        assert "Blunder audit UI: http://127.0.0.1:4567/" in output
