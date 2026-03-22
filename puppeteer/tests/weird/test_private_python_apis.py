"""Weird test: ratchet cross-module private Python helper imports."""

import ast
from functools import cache
from pathlib import Path

from tests.weird.repo_convention_helpers import PUPPETEER_DIR, REPO_ROOT

_PRIVATE_IMPORT_SCAN_ROOTS = (
    REPO_ROOT / "puppeteer" / "src",
    REPO_ROOT / "scripts",
    REPO_ROOT / "schemas",
)

_ALLOWED_PRIVATE_CROSS_MODULE_IMPORTS = {
    ("puppeteer.pilot", "puppeteer.pilot_bridge", "_record_tool_execution_failure"),
    ("puppeteer.pilot", "puppeteer.pilot_bridge", "_tool_execution_error_result"),
    ("puppeteer.pilot", "puppeteer.pilot_recovery", "_classify_permanent_llm_failure"),
    ("puppeteer.pilot", "puppeteer.pilot_recovery", "_handle_timeout"),
    ("puppeteer.pilot", "puppeteer.pilot_recovery", "_handle_truncated_response"),
    ("puppeteer.pilot", "puppeteer.pilot_recovery", "_recover_from_stall"),
    ("puppeteer.pilot", "puppeteer.pilot_rendering", "_fetch_state_summary"),
    ("puppeteer.pilot", "puppeteer.pilot_rendering", "_find_cache_breakpoint_idx"),
    ("puppeteer.pilot", "puppeteer.pilot_rendering", "_find_tool_name"),
    ("puppeteer.pilot", "puppeteer.pilot_rendering", "_message_text"),
    ("puppeteer.pilot", "puppeteer.pilot_rendering", "_summarize_tool_result"),
    ("puppeteer.pilot", "puppeteer.pilot_rendering", "_with_cache_control"),
}

_ALLOWED_PRIVATE_REEXPORTS = {
    ("puppeteer.orchestrator", "_check_regular_season_block"),
    ("puppeteer.orchestrator", "_ensure_game_over_event"),
    ("puppeteer.orchestrator", "_finalize_game"),
    ("puppeteer.orchestrator", "_git"),
    ("puppeteer.orchestrator", "_missing_llm_api_keys"),
    ("puppeteer.orchestrator", "_print_game_summary"),
    ("puppeteer.orchestrator", "_save_youtube_url"),
    ("puppeteer.orchestrator", "_setup_game"),
    ("puppeteer.orchestrator", "_update_website_youtube_url"),
    ("puppeteer.orchestrator", "_wait_for_all_games"),
    ("puppeteer.orchestrator", "_wait_for_game_start"),
    ("puppeteer.orchestrator", "_wait_for_spectator_table"),
    ("puppeteer.orchestrator", "_wait_with_pilot_monitoring"),
    ("puppeteer.orchestrator", "_write_error_log"),
    ("puppeteer.orchestrator", "_write_game_meta"),
}


def _module_name_for_path(path: Path) -> str:
    if path.is_relative_to(PUPPETEER_DIR / "src"):
        rel = path.relative_to(PUPPETEER_DIR / "src")
    else:
        rel = path.relative_to(REPO_ROOT)
    return ".".join(rel.with_suffix("").parts)


@cache
def _repo_python_modules() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for root in _PRIVATE_IMPORT_SCAN_ROOTS:
        for path in root.rglob("*.py"):
            modules[_module_name_for_path(path)] = path
    return modules


def _resolve_imported_module(importer_module: str, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module

    importer_package = importer_module.split(".")[:-1]
    levels_up = node.level - 1
    if levels_up > len(importer_package):
        return None
    base_parts = importer_package[: len(importer_package) - levels_up]
    if node.module is None:
        return ".".join(base_parts)
    return ".".join(base_parts + node.module.split("."))


@cache
def _private_cross_module_imports() -> frozenset[tuple[str, str, str]]:
    modules = _repo_python_modules()
    private_imports: set[tuple[str, str, str]] = set()

    for importer_module, path in modules.items():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            exporter_module = _resolve_imported_module(importer_module, node)
            if exporter_module is None or exporter_module not in modules:
                continue
            for alias in node.names:
                if alias.name.startswith("_") and exporter_module != importer_module:
                    private_imports.add((importer_module, exporter_module, alias.name))

    return frozenset(private_imports)


@cache
def _private_reexports() -> frozenset[tuple[str, str]]:
    reexports: set[tuple[str, str]] = set()

    for module_name, path in _repo_python_modules().items():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
                continue
            try:
                exported_names = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                continue
            if not isinstance(exported_names, list | tuple):
                continue
            for name in exported_names:
                if isinstance(name, str) and name.startswith("_"):
                    reexports.add((module_name, name))

    return frozenset(reexports)


class TestPrivatePythonApis:
    def test_no_new_cross_module_private_imports(self) -> None:
        unexpected = _private_cross_module_imports() - _ALLOWED_PRIVATE_CROSS_MODULE_IMPORTS
        assert not unexpected, (
            "New cross-module imports of underscore-prefixed helpers were added.\n"
            "If another module needs the helper, rename it to a public symbol in the owner module instead.\n  "
            + "\n  ".join(f"{importer} imports {exporter}.{name}" for importer, exporter, name in sorted(unexpected))
        )

    def test_no_new_private_reexports(self) -> None:
        unexpected = _private_reexports() - _ALLOWED_PRIVATE_REEXPORTS
        assert not unexpected, (
            "New underscore-prefixed names were added to __all__.\n"
            "Private helpers should not be part of a module's public export surface.\n  "
            + "\n  ".join(f"{module}.{name}" for module, name in sorted(unexpected))
        )
