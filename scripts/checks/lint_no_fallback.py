"""Lint for silent fallback patterns banned by AGENTS.md.

AGENTS.md says "never add graceful fallbacks, silent defaults, or
backwards-compatibility shims."  This script enforces that rule by
catching several pattern families using AST analysis.

Checked patterns:
  - `x or []`, `x or {}`, `x or ""` — silent fallback via boolean or
  - `.get(key, {})` / `.get(key, "")` — empty default hiding a missing key
  - `getattr(obj, attr, <non-None>)` — attribute fallback hiding a
    missing attribute
  - bare `except:` — catches everything including KeyboardInterrupt
  - `except Exception: pass` — silently swallows all errors

  - `.get(key, [])` — empty list default hiding a missing key

Patterns NOT checked (and why):
  - `or 0` / `or 0.0`: too many legitimate uses (nullable API token counts)
"""

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCAN_DIRS = [
    REPO_ROOT / "puppeteer" / "src",
    REPO_ROOT / "scripts",
]


def _is_empty_literal(node: ast.expr) -> str | None:
    """Return a description if `node` is [], {}, or ""; else None."""
    if isinstance(node, ast.List) and not node.elts:
        return "[]"
    if isinstance(node, ast.Dict) and not node.keys:
        return "{}"
    if isinstance(node, ast.Constant) and node.value == "" and isinstance(node.value, str):
        return '""'
    return None


def _check_file(path: Path, source_lines: list[str], repo_root: Path = REPO_ROOT) -> list[str]:
    """Return lint errors for a single file."""
    try:
        tree = ast.parse("".join(source_lines), filename=str(path))
    except SyntaxError:
        return []

    errors = []
    rel = path.relative_to(repo_root)

    for node in ast.walk(tree):
        # --- or [] / or {} / or "" ---
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            for value in node.values[1:]:
                desc = _is_empty_literal(value)
                if desc is None:
                    continue
                lineno = value.lineno
                errors.append(f"{rel}:{lineno}: or {desc} (silent fallback — restructure the code)")

        # --- .get(key, {}) ---
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Dict)
            and not node.args[1].keys
        ):
            lineno = node.args[1].lineno
            errors.append(f"{rel}:{lineno}: .get(key, {{}}) (silent default — use explicit key access or None check)")

        # --- .get(key, "") ---
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == ""
            and isinstance(node.args[1].value, str)
        ):
            lineno = node.args[1].lineno
            errors.append(f'{rel}:{lineno}: .get(key, "") (silent default — use explicit key access or None check)')

        # --- .get(key, []) ---
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.List)
            and not node.args[1].elts
        ):
            lineno = node.args[1].lineno
            errors.append(f"{rel}:{lineno}: .get(key, []) (silent default — use explicit key access or None check)")

        # --- getattr(obj, attr, <non-None>) ---
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 3
        ):
            default = node.args[2]
            is_none = isinstance(default, ast.Constant) and default.value is None
            if not is_none:
                lineno = default.lineno
                errors.append(
                    f"{rel}:{lineno}: getattr with non-None default (silent fallback — use explicit None check)"
                )

        # --- bare except: / except Exception: pass ---
        elif isinstance(node, ast.ExceptHandler):
            is_bare = node.type is None
            is_exception_pass = (
                isinstance(node.type, ast.Name)
                and node.type.id == "Exception"
                and len(node.body) == 1
                and isinstance(node.body[0], ast.Pass)
            )
            if is_bare:
                lineno = node.lineno
                errors.append(f"{rel}:{lineno}: bare except (catches KeyboardInterrupt — use specific exception)")
            elif is_exception_pass:
                lineno = node.lineno
                errors.append(
                    f"{rel}:{lineno}: except Exception: pass (silently swallows errors — handle or propagate)"
                )

    return errors


def lint_no_fallback() -> list[str]:
    errors = []
    for scan_dir in SCAN_DIRS:
        if not scan_dir.is_dir():
            continue
        for py_file in sorted(scan_dir.rglob("*.py")):
            if "__pycache__" in py_file.parts:
                continue
            source_lines = py_file.read_text().splitlines(keepends=True)
            errors.extend(_check_file(py_file, source_lines))
    return errors


def main() -> None:
    errors = lint_no_fallback()

    if errors:
        print("No-fallback lint errors (AGENTS.md: no silent defaults):", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        sys.exit(1)

    print("No-fallback: OK")


if __name__ == "__main__":
    main()
