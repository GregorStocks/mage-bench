"""Tests for Linux xvfb wrapping in the golden harness."""

import pytest

from tests import golden_helpers


def test_wrap_with_xvfb_prefixes_linux_commands(monkeypatch):
    monkeypatch.setattr(golden_helpers.sys, "platform", "linux")
    monkeypatch.setattr(
        golden_helpers.shutil,
        "which",
        lambda name: "/usr/bin/xvfb-run" if name == "xvfb-run" else None,
    )

    wrapped = golden_helpers.wrap_with_xvfb(["java", "-version"])

    assert wrapped[:3] == ["/usr/bin/xvfb-run", "--auto-servernum", "--server-args=-screen 0 1920x1080x24"]
    assert wrapped[3:] == ["java", "-version"]


def test_wrap_with_xvfb_leaves_non_linux_commands_unchanged(monkeypatch):
    monkeypatch.setattr(golden_helpers.sys, "platform", "darwin")

    wrapped = golden_helpers.wrap_with_xvfb(["java", "-version"])

    assert wrapped == ["java", "-version"]


def test_wrap_with_xvfb_requires_xvfb_on_linux(monkeypatch):
    monkeypatch.setattr(golden_helpers.sys, "platform", "linux")
    monkeypatch.setattr(golden_helpers.shutil, "which", lambda _name: None)

    with pytest.raises(AssertionError, match="xvfb-run"):
        golden_helpers.wrap_with_xvfb(["java", "-version"])
