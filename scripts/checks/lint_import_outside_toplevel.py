"""Lint for non-top-level imports outside the puppeteer project.

Ruff enforces PLC0415 for files linted from inside `puppeteer/`, but `scripts/`
and `schemas/` live outside that project root. This check covers those paths so
accidental function-local imports cannot slip past `make lint`.
"""

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCAN_DIRS = [
    REPO_ROOT / "scripts",
    REPO_ROOT / "schemas",
]

ALLOWED_IMPORTS: dict[Path, dict[int, str]] = {
    Path("schemas/migrations/v2_to_v3.py"): {
        16: "Migration uses current export helpers lazily to avoid import cycles.",
    },
    Path("schemas/migrations/v3_to_v4.py"): {
        13: "Migration reads harness epoch lazily so schemas do not import puppeteer on module load.",
    },
    Path("schemas/migrations/v6_to_v7.py"): {
        19: "Migration imports prior schema logic lazily to avoid cross-version import cycles.",
        20: "Migration imports current export helpers lazily to avoid cross-version import cycles.",
    },
    Path("schemas/migrations/v7_to_v8.py"): {
        22: "Migration imports schema coercion lazily to avoid cross-version import cycles.",
        23: "Migration imports annotation helpers lazily to avoid circular dependencies.",
    },
    Path("scripts/analysis/annotate_game.py"): {
        110: "Leaderboard regeneration is optional and only needed on the update path.",
    },
    Path("scripts/analysis/blunder_analysis.py"): {
        1060: "Shared annotation helpers are imported lazily to avoid circular analysis imports.",
        1287: "Game-path helper is only needed for CLI entrypoint resolution.",
        1304: "Leaderboard regeneration is optional and only needed for CLI update mode.",
    },
    Path("scripts/analysis/blunder_audit.py"): {
        132: "Game loading helper is only needed for the audit CLI path.",
        170: "Analysis metadata is imported lazily to avoid a circular analysis import.",
        178: "Analysis entrypoints are imported lazily to avoid a circular analysis import.",
    },
    Path("scripts/analysis/blunder_audit_web.py"): {
        261: "Web handler loads annotation helper lazily to avoid importing the audit CLI on startup.",
    },
    Path("scripts/analysis/blunder_seed.py"): {
        66: "Seed script only needs glob helper inside the CLI entrypoint.",
    },
    Path("scripts/export_game.py"): {
        220: "Scryfall lookups stay lazy so export helpers can be imported without network-oriented dependencies.",
        1282: "Season helper is imported lazily to avoid schema/export import cycles.",
        1349: "Leaderboard regeneration is optional and only needed after writing exports.",
    },
    Path("scripts/tournament_draft.py"): {
        79: "Draft loader imports tournament game logic lazily to avoid a tournament module cycle.",
    },
    Path("scripts/upload_youtube.py"): {
        151: "Google auth dependency is optional and should only be imported on upload paths.",
        152: "Google auth dependency is optional and should only be imported on upload paths.",
        153: "Google auth dependency is optional and should only be imported on upload paths.",
        154: "Google API client is optional and should only be imported on upload paths.",
        191: "Google API upload helpers are optional and only needed during uploads.",
        192: "Google API upload helpers are optional and only needed during uploads.",
    },
}


def _import_text(node: ast.Import | ast.ImportFrom) -> str:
    if isinstance(node, ast.Import):
        return "import " + ", ".join(alias.name for alias in node.names)
    module = node.module
    if module is None:
        module = ""
    return f"from {module} import " + ", ".join(alias.name for alias in node.names)


def _scope_name(node: ast.AST | None) -> str:
    if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
        return f"function `{node.name}`"
    if isinstance(node, ast.ClassDef):
        return f"class `{node.name}`"
    if isinstance(node, ast.If):
        return "`if` block"
    if isinstance(node, ast.Try):
        return "`try` block"
    if isinstance(node, ast.With):
        return "`with` block"
    return type(node).__name__ if node is not None else "nested scope"


def _check_file(
    path: Path,
    source_lines: list[str],
    *,
    repo_root: Path = REPO_ROOT,
    allowed_imports: dict[Path, dict[int, str]] = ALLOWED_IMPORTS,
) -> list[str]:
    try:
        tree = ast.parse("".join(source_lines), filename=str(path))
    except SyntaxError:
        return []

    parents: dict[ast.AST, ast.AST] = {}
    for ancestor in ast.walk(tree):
        for child in ast.iter_child_nodes(ancestor):
            parents[child] = ancestor

    rel_path = path.relative_to(repo_root)
    allowed_lines = allowed_imports.get(rel_path)
    if allowed_lines is None:
        allowed_lines = {}
    errors: list[str] = []

    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    for node in sorted(imports, key=lambda item: (item.lineno, item.col_offset)):
        parent_node = parents.get(node)
        if isinstance(parent_node, ast.Module):
            continue
        if node.lineno in allowed_lines:
            continue
        errors.append(
            f"{rel_path}:{node.lineno}: {_import_text(node)} inside {_scope_name(parent_node)}"
        )

    return errors


def lint_import_outside_toplevel() -> list[str]:
    errors: list[str] = []
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
    errors = lint_import_outside_toplevel()
    if errors:
        print(
            "Import-outside-toplevel lint errors (move the import or add an explicit allowlist entry):",
            file=sys.stderr,
        )
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        sys.exit(1)

    print("Import-outside-toplevel: OK")


if __name__ == "__main__":
    main()
