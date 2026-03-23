"""Tests for scripts/checks/quiet_check.py."""

import os
import shlex
import signal
import subprocess
from pathlib import Path

from scripts.checks.quiet_check import (
    PARALLEL_TARGETS,
    RECURSIVE_MAKE_ENV_VARS,
    SERIAL_TARGETS,
    TARGETS,
    _make_env,
    _run_command_with_captured_output,
    _run_make,
)


def test_make_check_routes_java_validation_through_lint_java() -> None:
    assert "lint-java" in TARGETS
    assert "verify-mcp-tools" not in TARGETS

    project_root = Path(__file__).resolve().parent.parent
    makefile = (project_root / "Makefile").read_text()

    assert ".PHONY: lint-java" in makefile
    assert "mvn -q -pl Mage.Client.Bridge -am -DskipTests -Pjava-lint verify" in makefile
    assert "$(MAKE) verify-mcp-tools" in makefile


def test_make_lint_uses_root_ruff_config() -> None:
    project_root = Path(__file__).resolve().parent.parent
    makefile = (project_root / "Makefile").read_text()
    lint_cmd = "uv run --project puppeteer ruff check --config ruff-lint.toml puppeteer/ scripts/ schemas/ src/ tests/"
    lint_fix_cmd = (
        "uv run --project puppeteer ruff check --config ruff-lint.toml --fix puppeteer/ scripts/ schemas/ src/ tests/"
    )

    assert lint_cmd in makefile
    assert lint_fix_cmd in makefile


def test_make_python_checks_include_src_and_tests_tree() -> None:
    project_root = Path(__file__).resolve().parent.parent
    makefile = (project_root / "Makefile").read_text()

    assert "uv run --project puppeteer ruff format puppeteer/ scripts/ schemas/ src/ tests/" in makefile
    assert "uv run --project puppeteer ruff format --check puppeteer/ scripts/ schemas/ src/ tests/" in makefile
    assert (
        "uv run --project puppeteer mypy --config-file puppeteer/pyproject.toml "
        "puppeteer/src/puppeteer/ scripts/ schemas/ src/magebench/"
    ) in makefile


def test_make_check_serializes_website_targets() -> None:
    assert SERIAL_TARGETS == [
        "lint-website",
        "lint-md",
        "astro-check",
        "test-js",
        "verify-schema-types",
    ]
    assert sorted(PARALLEL_TARGETS + SERIAL_TARGETS) == sorted(TARGETS)
    assert set(PARALLEL_TARGETS).isdisjoint(SERIAL_TARGETS)


def test_website_install_stamp_uses_npm_ci() -> None:
    project_root = Path(__file__).resolve().parent.parent
    makefile = (project_root / "Makefile").read_text()

    assert "npm ci --prefer-offline --no-audit --no-fund" in makefile
    assert "npm install --prefer-offline --no-audit --no-fund" not in makefile


def test_make_env_strips_recursive_make_state(monkeypatch) -> None:
    monkeypatch.setenv("KEEP_ME", "1")
    for key in RECURSIVE_MAKE_ENV_VARS:
        monkeypatch.setenv(key, "set")

    env = _make_env()

    assert env["KEEP_ME"] == "1"
    for key in RECURSIVE_MAKE_ENV_VARS:
        assert key not in env


def test_run_make_live_output_uses_clean_env(monkeypatch) -> None:
    called: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        called["args"] = args
        called["kwargs"] = kwargs
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr("scripts.checks.quiet_check.subprocess.run", fake_run)

    _run_make("lint", capture_output=False)

    assert called["args"] == (["make", "lint"],)
    assert called["kwargs"]["capture_output"] is False
    assert called["kwargs"]["text"] is True
    assert called["kwargs"]["env"] == _make_env()


def test_run_make_capture_uses_clean_env(monkeypatch) -> None:
    called: dict[str, object] = {}

    class FakePopen:
        def __init__(self, *args, **kwargs):
            called["args"] = args
            called["kwargs"] = kwargs

        def wait(self) -> int:
            return 0

    monkeypatch.setattr("scripts.checks.quiet_check.subprocess.Popen", FakePopen)

    result = _run_make("lint", capture_output=True)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert called["args"] == (["make", "lint"],)
    assert called["kwargs"]["env"] == _make_env()
    assert called["kwargs"]["stderr"] == subprocess.STDOUT


def test_captured_output_returns_after_child_exit_even_if_descendant_holds_fd(
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / "sleep.pid"
    command = [
        "sh",
        "-c",
        f"sleep 5 & echo $! > {shlex.quote(str(pid_path))}; echo child-exiting",
    ]

    result = _run_command_with_captured_output(command, env=os.environ.copy())

    assert result.returncode == 0
    assert result.stdout == "child-exiting\n"
    descendant_pid = int(pid_path.read_text().strip())
    try:
        os.kill(descendant_pid, 0)
    except ProcessLookupError as exc:
        raise AssertionError("captured output blocked until the descendant exited") from exc
    finally:
        try:
            os.kill(descendant_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
