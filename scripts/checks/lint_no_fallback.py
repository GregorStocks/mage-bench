"""Lint for silent fallback patterns banned by AGENTS.md.

AGENTS.md says "never add graceful fallbacks, silent defaults, or
backwards-compatibility shims."  This script enforces a subset of that
rule by catching `or []`, `or {}`, and `or ""` patterns using AST
analysis.  There is no suppression mechanism — restructure the code instead.

Patterns NOT checked (and why):
  - `or 0` / `or 0.0`: too many legitimate uses (nullable API token counts)
  - `except Exception`: mix of cleanup/reraise/logging; ruff BLE001 exists
    but false-positive rate is too high for blanket enforcement
  - `.get(key, default)`: 200+ matches, overwhelmingly legitimate
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
        return "or []"
    if isinstance(node, ast.Dict) and not node.keys:
        return "or {}"
    if (
        isinstance(node, ast.Constant)
        and node.value == ""
        and isinstance(node.value, str)
    ):
        return 'or ""'
    return None


def _check_file(path: Path, source_lines: list[str]) -> list[str]:
    """Return lint errors for a single file."""
    try:
        tree = ast.parse("".join(source_lines), filename=str(path))
    except SyntaxError:
        return []

    errors = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
            continue
        # Check all values except the first (the first is the "real" value,
        # the rest are fallbacks).
        for value in node.values[1:]:
            desc = _is_empty_literal(value)
            if desc is None:
                continue
            lineno = value.lineno
            rel = path.relative_to(REPO_ROOT)
            errors.append(
                f"{rel}:{lineno}: {desc} (silent fallback — restructure the code)"
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
        print(
            "No-fallback lint errors (AGENTS.md: no silent defaults):", file=sys.stderr
        )
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        sys.exit(1)

    print("No-fallback: OK")


if __name__ == "__main__":
    main()
