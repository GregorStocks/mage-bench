"""Smoke test: every module in magebench.analysis.toolbox is importable."""

import importlib
from pathlib import Path

TOOLBOX_DIR = Path(__file__).resolve().parent.parent / "src" / "magebench" / "analysis" / "toolbox"

# dump_sample_prompt runs code at module scope that requires a specific game
# file on disk, so it can't be import-tested in CI.
SKIP_MODULES = {"__init__", "dump_sample_prompt"}


def test_all_toolbox_modules_importable() -> None:
    """Import every .py file in the toolbox to catch syntax/import errors."""
    modules = sorted(p.stem for p in TOOLBOX_DIR.glob("*.py") if p.stem not in SKIP_MODULES)
    assert modules, f"No modules found in {TOOLBOX_DIR}"

    failures: list[str] = []
    for name in modules:
        module_path = f"magebench.analysis.toolbox.{name}"
        try:
            importlib.import_module(module_path)
        except ImportError as exc:
            failures.append(f"{module_path}: {exc}")

    assert not failures, "Toolbox import failures:\n" + "\n".join(failures)
