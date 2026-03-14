"""Tests for scripts/checks/quiet_check.py."""

import subprocess
from pathlib import Path

from scripts.checks.quiet_check import (
    PARALLEL_TARGETS,
    RECURSIVE_MAKE_ENV_VARS,
    SERIAL_TARGETS,
    TARGETS,
    _make_env,
    _run_make,
)


def test_make_check_routes_java_validation_through_lint_java() -> None:
    assert "lint-java" in TARGETS
    assert "verify-mcp-tools" not in TARGETS

    project_root = Path(__file__).resolve().parent.parent.parent
    makefile = (project_root / "Makefile").read_text()

    assert ".PHONY: lint-java" in makefile
    assert "mvn -q -pl Mage.Client.Bridge -am -DskipTests -Pjava-lint verify" in makefile
    assert "$(MAKE) verify-mcp-tools" in makefile


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


def test_make_env_strips_recursive_make_state(monkeypatch) -> None:
    monkeypatch.setenv("KEEP_ME", "1")
    for key in RECURSIVE_MAKE_ENV_VARS:
        monkeypatch.setenv(key, "set")

    env = _make_env()

    assert env["KEEP_ME"] == "1"
    for key in RECURSIVE_MAKE_ENV_VARS:
        assert key not in env


def test_run_make_uses_clean_env(monkeypatch) -> None:
    called: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        called["args"] = args
        called["kwargs"] = kwargs
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr("scripts.checks.quiet_check.subprocess.run", fake_run)

    _run_make("lint", capture_output=True)

    assert called["args"] == (["make", "lint"],)
    assert called["kwargs"]["capture_output"] is True
    assert called["kwargs"]["text"] is True
    assert called["kwargs"]["env"] == _make_env()
