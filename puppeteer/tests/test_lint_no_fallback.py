"""Tests for scripts/checks/lint_no_fallback.py."""

from pathlib import Path

from scripts.checks.lint_no_fallback import _check_file, lint_no_fallback

_TEST_DIR = Path("/lint-test")


def _check(source: str) -> list[str]:
    """Run _check_file on a source string, return error messages."""
    lines = source.splitlines(keepends=True)
    return _check_file(_TEST_DIR / "test.py", lines, repo_root=_TEST_DIR)


# --- or [] / or {} / or "" ---


def test_catches_or_empty_list() -> None:
    errors = _check("x = y or []\n")
    assert len(errors) == 1
    assert "or []" in errors[0]


def test_catches_or_empty_dict() -> None:
    errors = _check("x = y or {}\n")
    assert len(errors) == 1
    assert "or {}" in errors[0]


def test_catches_or_empty_string() -> None:
    errors = _check('x = y or ""\n')
    assert len(errors) == 1
    assert 'or ""' in errors[0]


def test_ignores_or_nonempty_list() -> None:
    assert _check("x = y or [1]\n") == []


def test_ignores_or_zero() -> None:
    assert _check("x = y or 0\n") == []


# --- .get(key, {}) ---


def test_catches_get_empty_dict() -> None:
    errors = _check("x = d.get('k', {})\n")
    assert len(errors) == 1
    assert ".get(key, {})" in errors[0]


def test_ignores_get_none() -> None:
    assert _check("x = d.get('k', None)\n") == []


def test_ignores_get_zero() -> None:
    assert _check("x = d.get('k', 0)\n") == []


def test_ignores_get_single_arg() -> None:
    assert _check("x = d.get('k')\n") == []


def test_ignores_get_empty_list() -> None:
    """Empty list defaults are not yet checked (planned for follow-up)."""
    assert _check("x = d.get('k', [])\n") == []


def test_ignores_get_empty_string() -> None:
    """Empty string defaults are not yet checked (planned for follow-up)."""
    assert _check('x = d.get("k", "")\n') == []


# --- getattr(obj, attr, <non-None>) ---


def test_catches_getattr_with_zero() -> None:
    errors = _check("x = getattr(obj, 'a', 0)\n")
    assert len(errors) == 1
    assert "getattr with non-None default" in errors[0]


def test_catches_getattr_with_string() -> None:
    errors = _check("x = getattr(obj, 'a', 'fallback')\n")
    assert len(errors) == 1
    assert "getattr with non-None default" in errors[0]


def test_ignores_getattr_with_none() -> None:
    assert _check("x = getattr(obj, 'a', None)\n") == []


def test_ignores_getattr_two_args() -> None:
    assert _check("x = getattr(obj, 'a')\n") == []


# --- bare except / except Exception: pass ---


def test_catches_bare_except() -> None:
    source = "try:\n    x()\nexcept:\n    log()\n"
    errors = _check(source)
    assert len(errors) == 1
    assert "bare except" in errors[0]


def test_catches_except_exception_pass() -> None:
    source = "try:\n    x()\nexcept Exception:\n    pass\n"
    errors = _check(source)
    assert len(errors) == 1
    assert "except Exception: pass" in errors[0]


def test_ignores_except_exception_with_body() -> None:
    source = "try:\n    x()\nexcept Exception:\n    log(e)\n"
    assert _check(source) == []


def test_ignores_specific_exception() -> None:
    source = "try:\n    x()\nexcept ValueError:\n    pass\n"
    assert _check(source) == []


# --- multiple violations ---


def test_multiple_violations_in_one_file() -> None:
    source = "a = x or []\nb = d.get('k', {})\n"
    errors = _check(source)
    assert len(errors) == 2


# --- integration ---


def test_real_codebase_passes() -> None:
    """The actual codebase must pass the no-fallback lint."""
    errors = lint_no_fallback()
    assert errors == [], "No-fallback lint errors:\n" + "\n".join(errors)
