"""Weird test: ratchet the `src/magebench` package dependency DAG."""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

from tests.weird.repo_convention_helpers import MAGEBENCH_DIR

_COMPONENT_DEPENDENCIES = {
    "common": frozenset(),
    "game": frozenset({"common"}),
    "analysis": frozenset({"common", "game"}),
    "leaderboard": frozenset({"common", "game"}),
    "pilot": frozenset({"common", "game"}),
    "orchestration": frozenset({"analysis", "common", "game", "leaderboard", "pilot"}),
    "cli": frozenset({"analysis", "common", "game", "leaderboard", "orchestration", "pilot"}),
}


def _module_name_for_path(path: Path) -> str:
    rel = path.relative_to(MAGEBENCH_DIR).with_suffix("")
    parts = rel.parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("magebench", *parts)) if parts else "magebench"


def _importer_package_parts(importer_module: str, importer_path: Path) -> list[str]:
    importer_parts = importer_module.split(".")
    return importer_parts if importer_path.name == "__init__.py" else importer_parts[:-1]


def _is_beyond_top_level_relative_import(importer_module: str, importer_path: Path, node: ast.ImportFrom) -> bool:
    if node.level == 0:
        return False

    importer_package = _importer_package_parts(importer_module, importer_path)
    levels_up = node.level - 1
    return levels_up >= len(importer_package)


def _resolve_imported_module(importer_module: str, importer_path: Path, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    if _is_beyond_top_level_relative_import(importer_module, importer_path, node):
        return None

    importer_package = _importer_package_parts(importer_module, importer_path)
    levels_up = node.level - 1
    base_parts = importer_package[: len(importer_package) - levels_up]
    if node.module is None:
        return ".".join(base_parts)
    return ".".join(base_parts + node.module.split("."))


@cache
def _magebench_modules() -> dict[str, Path]:
    return {_module_name_for_path(path): path for path in MAGEBENCH_DIR.rglob("*.py")}


def _is_internal_package_or_module(candidate: str, modules: dict[str, Path]) -> bool:
    return candidate in modules or any(name.startswith(candidate + ".") for name in modules)


def _component_for_module(module_name: str) -> str | None:
    if not module_name.startswith("magebench"):
        return None
    parts = module_name.split(".")
    if len(parts) < 2:
        return None
    return parts[1]


@cache
def _component_dependencies() -> frozenset[tuple[str, str, str, str]]:
    modules = _magebench_modules()
    result: set[tuple[str, str, str, str]] = set()

    for importer_module, path in modules.items():
        importer_component = _component_for_module(importer_module)
        if importer_component is None:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_component = _component_for_module(alias.name)
                    if imported_component is None:
                        continue
                    result.add(
                        (
                            importer_component,
                            imported_component,
                            importer_module,
                            alias.name,
                        )
                    )
                continue

            if not isinstance(node, ast.ImportFrom):
                continue
            base_module = _resolve_imported_module(importer_module, path, node)
            if base_module is None or not base_module.startswith("magebench"):
                continue
            for alias in node.names:
                if alias.name == "*":
                    target_module = base_module
                else:
                    candidate = f"{base_module}.{alias.name}"
                    target_module = candidate if _is_internal_package_or_module(candidate, modules) else base_module
                imported_component = _component_for_module(target_module)
                if imported_component is None:
                    continue
                result.add(
                    (
                        importer_component,
                        imported_component,
                        importer_module,
                        target_module,
                    )
                )

    return frozenset(result)


@cache
def _invalid_relative_imports() -> frozenset[tuple[str, int, str]]:
    invalid_imports: set[tuple[str, int, str]] = set()

    for importer_module, path in _magebench_modules().items():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if not _is_beyond_top_level_relative_import(importer_module, path, node):
                continue
            module = node.module or ""
            dots = "." * node.level
            invalid_imports.add((importer_module, node.lineno, f"from {dots}{module} import ..."))

    return frozenset(invalid_imports)


class TestMagebenchPackageDag:
    def test_no_relative_imports_beyond_top_level_package(self) -> None:
        assert not _invalid_relative_imports(), (
            "`src/magebench` contains relative imports beyond the top-level package.\n"
            "Use an absolute `magebench.*` import or a valid in-package relative import instead.\n  "
            + "\n  ".join(
                f"{module}:{lineno}: {statement}" for module, lineno, statement in sorted(_invalid_relative_imports())
            )
        )

    def test_top_level_components_match_declared_dag(self) -> None:
        actual_components = {
            path.name for path in MAGEBENCH_DIR.iterdir() if path.is_dir() and (path / "__init__.py").exists()
        }
        assert actual_components == set(_COMPONENT_DEPENDENCIES), (
            "Top-level `src/magebench` packages changed.\n"
            "Update the package DAG test and README when adding or renaming components.\n  "
            + "\n  ".join(sorted(actual_components ^ set(_COMPONENT_DEPENDENCIES)))
        )

    def test_component_imports_follow_declared_dag(self) -> None:
        violations = [
            (importer_component, imported_component, importer_module, imported_module)
            for importer_component, imported_component, importer_module, imported_module in _component_dependencies()
            if importer_component != imported_component
            and imported_component not in _COMPONENT_DEPENDENCIES[importer_component]
        ]
        assert not violations, (
            "`src/magebench` package dependencies violated the declared DAG.\n"
            "Move shared code downward or split it into a lower-level package instead of creating a cycle.\n  "
            + "\n  ".join(
                f"{importer_module} ({importer_component}) imports {imported_module} ({imported_component})"
                for importer_component, imported_component, importer_module, imported_module in sorted(violations)
            )
        )
