"""Smoke test: every module in magebench.orchestration is importable."""

import importlib
from pathlib import Path

ORCHESTRATION_DIR = Path(__file__).resolve().parent.parent / "src" / "magebench" / "orchestration"

SKIP_MODULES = {"__init__"}


def test_all_orchestration_modules_importable() -> None:
    """Import every .py file in the orchestration package."""
    modules = sorted(path.stem for path in ORCHESTRATION_DIR.glob("*.py") if path.stem not in SKIP_MODULES)
    assert modules, f"No modules found in {ORCHESTRATION_DIR}"

    failures: list[str] = []
    for name in modules:
        module_path = f"magebench.orchestration.{name}"
        try:
            importlib.import_module(module_path)
        except ImportError as exc:
            failures.append(f"{module_path}: {exc}")

    assert not failures, "Orchestration import failures:\n" + "\n".join(failures)
