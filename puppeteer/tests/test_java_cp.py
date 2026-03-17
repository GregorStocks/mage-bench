"""Tests for java -cp classpath computation and JVM command building."""

import sys
from unittest.mock import patch

from tests.golden_helpers import (
    _build_java_cmd,
    _classpath_cache,
    _find_reactor_modules,
    _reactor_module_cache,
    _replace_reactor_jars,
    compute_module_classpath,
)


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


def _write_pom(directory, artifact_id, modules=None):
    """Write a minimal pom.xml with the given artifactId and optional child modules."""
    directory.mkdir(parents=True, exist_ok=True)
    module_xml = ""
    if modules:
        entries = "\n".join(f"        <module>{m}</module>" for m in modules)
        module_xml = f"\n    <modules>\n{entries}\n    </modules>"
    (directory / "pom.xml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<project>
    <parent><artifactId>parent</artifactId></parent>
    <artifactId>{artifact_id}</artifactId>{module_xml}
</project>
"""
    )


def test_find_reactor_modules(tmp_path):
    """Verify _find_reactor_modules walks the reactor and maps artifactId to target/classes."""
    # Root pom with two child modules
    _write_pom(tmp_path, "root", modules=["ModA", "ModB"])

    # ModA: has target/classes → should appear in map
    _write_pom(tmp_path / "ModA", "mod-a")
    (tmp_path / "ModA" / "target" / "classes").mkdir(parents=True)

    # ModB: no target/classes → should NOT appear
    _write_pom(tmp_path / "ModB", "mod-b")

    try:
        result = _find_reactor_modules(tmp_path)
        assert "mod-a" in result
        assert result["mod-a"] == tmp_path / "ModA" / "target" / "classes"
        assert "mod-b" not in result
        # Root itself has no target/classes, should not appear
        assert "root" not in result
    finally:
        _reactor_module_cache.pop(tmp_path, None)


def test_find_reactor_modules_nested(tmp_path):
    """Verify _find_reactor_modules handles nested submodules."""
    _write_pom(tmp_path, "root", modules=["Parent"])
    _write_pom(tmp_path / "Parent", "parent-mod", modules=["Child"])
    _write_pom(tmp_path / "Parent" / "Child", "child-mod")
    (tmp_path / "Parent" / "Child" / "target" / "classes").mkdir(parents=True)

    try:
        result = _find_reactor_modules(tmp_path)
        assert "child-mod" in result
        assert result["child-mod"] == tmp_path / "Parent" / "Child" / "target" / "classes"
    finally:
        _reactor_module_cache.pop(tmp_path, None)


def test_find_reactor_modules_cached(tmp_path):
    """Verify _find_reactor_modules returns cached result on second call."""
    _write_pom(tmp_path, "root", modules=["Mod"])
    _write_pom(tmp_path / "Mod", "mod-x")
    (tmp_path / "Mod" / "target" / "classes").mkdir(parents=True)

    try:
        first = _find_reactor_modules(tmp_path)
        second = _find_reactor_modules(tmp_path)
        assert first is second
    finally:
        _reactor_module_cache.pop(tmp_path, None)


def test_replace_reactor_jars(tmp_path):
    """Verify _replace_reactor_jars swaps ~/.m2/repository JARs for target/classes."""
    _write_pom(tmp_path, "root", modules=["Mage", "Mage.Common"])
    _write_pom(tmp_path / "Mage", "mage")
    (tmp_path / "Mage" / "target" / "classes").mkdir(parents=True)
    _write_pom(tmp_path / "Mage.Common", "mage-common")
    (tmp_path / "Mage.Common" / "target" / "classes").mkdir(parents=True)

    m2_mage = "/home/user/.m2/repository/org/mage/mage/1.4.58/mage-1.4.58.jar"
    m2_common = "/home/user/.m2/repository/org/mage/mage-common/1.4.58/mage-common-1.4.58.jar"
    external = "/home/user/.m2/repository/com/google/guava/guava-31.1.jar"

    classpath = f"{m2_mage}:{m2_common}:{external}"

    try:
        result = _replace_reactor_jars(classpath, tmp_path)
        entries = result.split(":")
        assert entries[0] == str(tmp_path / "Mage" / "target" / "classes")
        assert entries[1] == str(tmp_path / "Mage.Common" / "target" / "classes")
        assert entries[2] == external
    finally:
        _reactor_module_cache.pop(tmp_path, None)


def test_replace_reactor_jars_no_modules(tmp_path):
    """Verify _replace_reactor_jars is a no-op when no reactor modules exist."""
    _write_pom(tmp_path, "root")

    external = "/home/user/.m2/repository/com/google/guava/guava-31.1.jar"

    try:
        result = _replace_reactor_jars(external, tmp_path)
        assert result == external
    finally:
        _reactor_module_cache.pop(tmp_path, None)


def test_build_java_cmd_max_heap():
    """Verify -Xmx flag is included when max_heap is set."""
    cmd = _build_java_cmd("/cp", "Main", {}, max_heap="256m")
    assert "-Xmx256m" in cmd
    # -Xmx should come before -cp
    assert cmd.index("-Xmx256m") < cmd.index("-cp")


def test_build_java_cmd_no_heap_by_default():
    """Verify no -Xmx flag when max_heap is not specified."""
    cmd = _build_java_cmd("/cp", "Main", {})
    assert not any(arg.startswith("-Xmx") for arg in cmd)


def test_build_java_cmd_max_metaspace():
    """Verify -XX:MaxMetaspaceSize flag is included when max_metaspace is set."""
    cmd = _build_java_cmd("/cp", "Main", {}, max_metaspace="128m")
    assert "-XX:MaxMetaspaceSize=128m" in cmd
    assert cmd.index("-XX:MaxMetaspaceSize=128m") < cmd.index("-cp")


def test_build_java_cmd_no_metaspace_by_default():
    """Verify no MaxMetaspaceSize flag when max_metaspace is not specified."""
    cmd = _build_java_cmd("/cp", "Main", {})
    assert not any(arg.startswith("-XX:MaxMetaspaceSize") for arg in cmd)
