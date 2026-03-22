"""Convention test ratcheting legacy top-level `__all__` usage."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.weird.repo_convention_helpers import REPO_ROOT

_SEARCH_ROOTS = (
    REPO_ROOT / "puppeteer" / "src",
    REPO_ROOT / "scripts",
    REPO_ROOT / "schemas",
    REPO_ROOT / "src",
)

_ALLOWED_DUNDER_ALL_FILES = frozenset()


def _has_top_level_dunder_all(path: Path) -> bool:
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    return True
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "__all__":
            return True
    return False


def _dunder_all_files() -> set[str]:
    actual: set[str] = set()
    for root in _SEARCH_ROOTS:
        for path in root.rglob("*.py"):
            if _has_top_level_dunder_all(path):
                actual.add(str(path.relative_to(REPO_ROOT)))
    return actual


class TestDunderAllUsageRatchet:
    def test_dunder_all_allowlist_matches_repo(self) -> None:
        actual = _dunder_all_files()
        unexpected = actual - _ALLOWED_DUNDER_ALL_FILES
        missing = _ALLOWED_DUNDER_ALL_FILES - actual

        assert actual == _ALLOWED_DUNDER_ALL_FILES, (
            "Legacy top-level `__all__` usage changed.\n"
            "Do not add new `__all__` exports; import the concrete module you need instead.\n"
            "If you removed one of the allowed legacy cases, update this ratchet too.\n"
            + ("Unexpected new files:\n  " + "\n  ".join(sorted(unexpected)) + "\n" if unexpected else "")
            + ("Removed allowlisted files:\n  " + "\n  ".join(sorted(missing)) if missing else "")
        )
