"""Tests for scripts/checks/lint_import_outside_toplevel.py."""

from pathlib import Path

from scripts.checks.lint_import_outside_toplevel import (
    _check_file,
    lint_import_outside_toplevel,
)

_TEST_DIR = Path("/lint-test")


def _check(
    source: str,
    *,
    filename: str = "test.py",
    allowed_imports: dict[Path, dict[int, str]] | None = None,
) -> list[str]:
    lines = source.splitlines(keepends=True)
    return _check_file(
        _TEST_DIR / filename,
        lines,
        repo_root=_TEST_DIR,
        allowed_imports={} if allowed_imports is None else allowed_imports,
    )


def test_allows_top_level_import() -> None:
    assert _check("import json\n\n\ndef f():\n    return json.loads('{}')\n") == []


def test_catches_function_local_import() -> None:
    errors = _check("def f():\n    import json\n    return json.loads('{}')\n")
    assert errors == ["test.py:2: import json inside function `f`"]


def test_catches_conditional_import() -> None:
    errors = _check("if flag:\n    from pkg import thing\n")
    assert errors == ["test.py:2: from pkg import thing inside `if` block"]


def test_allows_explicit_allowlist_entry() -> None:
    errors = _check(
        "def f():\n    import json\n    return json.loads('{}')\n",
        allowed_imports={Path("test.py"): {2: "Intentional lazy import for test."}},
    )
    assert errors == []


def test_real_codebase_passes() -> None:
    errors = lint_import_outside_toplevel()
    assert errors == [], "Import-outside-toplevel lint errors:\n" + "\n".join(errors)
