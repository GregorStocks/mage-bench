"""Smoke test: every module in magebench.pilot is importable."""

import importlib
from pathlib import Path

PILOT_DIR = Path(__file__).resolve().parent.parent / "src" / "magebench" / "pilot"

SKIP_MODULES = {"__init__"}


def test_all_pilot_modules_importable() -> None:
    """Import every .py file in the pilot package to catch syntax/import errors."""
    modules = sorted(p.stem for p in PILOT_DIR.glob("*.py") if p.stem not in SKIP_MODULES)
    assert modules, f"No modules found in {PILOT_DIR}"

    failures: list[str] = []
    for name in modules:
        module_path = f"magebench.pilot.{name}"
        try:
            importlib.import_module(module_path)
        except ImportError as exc:
            failures.append(f"{module_path}: {exc}")

    assert not failures, "Pilot import failures:\n" + "\n".join(failures)
