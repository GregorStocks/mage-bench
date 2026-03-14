"""Tests for scripts/checks/quiet_check.py."""

from pathlib import Path

from scripts.checks.quiet_check import TARGETS


def test_make_check_routes_java_validation_through_lint_java() -> None:
    assert "lint-java" in TARGETS
    assert "verify-mcp-tools" not in TARGETS

    project_root = Path(__file__).resolve().parent.parent.parent
    makefile = (project_root / "Makefile").read_text()

    assert ".PHONY: lint-java" in makefile
    assert "mvn -q -pl Mage.Client.Bridge -DskipTests -Pjava-lint verify" in makefile
    assert "$(MAKE) verify-mcp-tools" in makefile
