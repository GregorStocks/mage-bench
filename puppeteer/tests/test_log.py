"""Tests for the puppeteer.log module."""

import logging
import tempfile
from pathlib import Path
from unittest.mock import patch

import puppeteer.log
from puppeteer.log import _PuppeteerFormatter, get_logger, log_error, setup_logging


def _reset_logging():
    """Reset the module-level setup guard so setup_logging can be called again."""
    puppeteer.log._setup_done = False
    # Remove any handlers added by previous setup_logging calls
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)  # default


class TestPuppeteerFormatter:
    def test_info_format_no_level_tag(self):
        record = logging.LogRecord("test", logging.INFO, "", 0, "hello world", (), None)
        fmt = _PuppeteerFormatter()
        result = fmt.format(record)
        assert result.endswith("] hello world")
        assert "[INFO]" not in result

    def test_debug_format_no_level_tag(self):
        record = logging.LogRecord("test", logging.DEBUG, "", 0, "debug msg", (), None)
        fmt = _PuppeteerFormatter()
        result = fmt.format(record)
        assert result.endswith("] debug msg")
        assert "[DEBUG]" not in result

    def test_warning_format_has_level_tag(self):
        record = logging.LogRecord("test", logging.WARNING, "", 0, "warn msg", (), None)
        fmt = _PuppeteerFormatter()
        result = fmt.format(record)
        assert "[WARNING]" in result
        assert "warn msg" in result

    def test_error_format_has_level_tag(self):
        record = logging.LogRecord("test", logging.ERROR, "", 0, "err msg", (), None)
        fmt = _PuppeteerFormatter()
        result = fmt.format(record)
        assert "[ERROR]" in result
        assert "err msg" in result

    def test_timestamp_present(self):
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        fmt = _PuppeteerFormatter()
        result = fmt.format(record)
        # Should start with [HH:MM:SS]
        assert result[0] == "["
        assert result[9] == "]"


class TestSetupLogging:
    def setup_method(self):
        _reset_logging()

    def teardown_method(self):
        _reset_logging()

    def test_default_level_is_info(self):
        setup_logging()
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_debug_flag(self):
        setup_logging(debug=True)
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_env_var_overrides(self):
        with patch.dict("os.environ", {"PUPPETEER_LOG_LEVEL": "WARNING"}):
            setup_logging()
        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_env_var_debug(self):
        with patch.dict("os.environ", {"PUPPETEER_LOG_LEVEL": "DEBUG"}):
            setup_logging()
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_idempotent(self):
        setup_logging()
        root = logging.getLogger()
        handler_count = len(root.handlers)
        setup_logging()  # second call should be a no-op
        assert len(root.handlers) == handler_count

    def test_adds_stdout_handler(self):
        setup_logging()
        root = logging.getLogger()
        assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)


class TestGetLogger:
    def test_returns_named_logger(self):
        lg = get_logger("test.module")
        assert lg.name == "test.module"
        assert isinstance(lg, logging.Logger)


class TestLogError:
    def test_writes_error_file(self):
        _reset_logging()
        setup_logging()
        lg = get_logger("test_log_error")
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = Path(tmp)
            log_error(lg, game_dir, "player1", "something broke")
            error_file = game_dir / "player1_errors.log"
            assert error_file.exists()
            content = error_file.read_text()
            assert "something broke" in content

    def test_no_game_dir(self):
        _reset_logging()
        setup_logging()
        lg = get_logger("test_log_error_none")
        # Should not raise when game_dir is None
        log_error(lg, None, "player1", "error without dir")

    def test_appends_to_existing_file(self):
        _reset_logging()
        setup_logging()
        lg = get_logger("test_log_error_append")
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = Path(tmp)
            log_error(lg, game_dir, "p1", "first error")
            log_error(lg, game_dir, "p1", "second error")
            content = (game_dir / "p1_errors.log").read_text()
            assert "first error" in content
            assert "second error" in content
