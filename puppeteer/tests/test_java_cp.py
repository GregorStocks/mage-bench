"""Tests for java -cp classpath computation and JVM command building."""

import sys
from unittest.mock import patch

from tests.golden_helpers import _build_java_cmd, _classpath_cache, compute_module_classpath


def test_build_java_cmd_basic():
    cmd = _build_java_cmd("/some/classpath.jar", "com.example.Main", {})
    assert cmd[0] == "java"
    assert "--add-opens=java.base/java.io=ALL-UNNAMED" in cmd
    assert cmd[-3:] == ["-cp", "/some/classpath.jar", "com.example.Main"]


def test_build_java_cmd_system_props():
    cmd = _build_java_cmd("/cp", "Main", {"foo": "bar", "baz": "qux"})
    assert "-Dfoo=bar" in cmd
    assert "-Dbaz=qux" in cmd
    # System props come before -cp
    cp_idx = cmd.index("-cp")
    foo_idx = cmd.index("-Dfoo=bar")
    assert foo_idx < cp_idx


def test_build_java_cmd_darwin_flag():
    with patch.object(sys, "platform", "darwin"):
        cmd = _build_java_cmd("/cp", "Main", {})
        assert "-Dapple.awt.UIElement=true" in cmd


def test_build_java_cmd_linux_no_darwin_flag():
    with patch.object(sys, "platform", "linux"):
        cmd = _build_java_cmd("/cp", "Main", {})
        assert "-Dapple.awt.UIElement=true" not in cmd


def test_compute_module_classpath_caching(tmp_path):
    """Verify that compute_module_classpath caches results per module."""
    # Pre-populate the cache to avoid running mvn
    _classpath_cache["TestModule"] = "/cached/classpath"
    try:
        result = compute_module_classpath(tmp_path, "TestModule")
        assert result == "/cached/classpath"
    finally:
        del _classpath_cache["TestModule"]
