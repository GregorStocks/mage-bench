"""Convention tests for temporary shim expiration metadata."""

import re
from datetime import date
from pathlib import Path

from tests.weird.repo_convention_helpers import REPO_ROOT

_CODE_EXTENSIONS = {".bash", ".java", ".kt", ".kts", ".py", ".sh", ".zsh"}
_SKIP_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "node_modules",
    "target",
}
_SHIM_TODO_RE = re.compile(r"TODO\(shim\):(?P<body>[^\n]*)")
_EXPIRES_RE = re.compile(r"(?:^|\s)expires=(?P<expires>\S+)")


def _issue_slug(issue_path: Path) -> str:
    prefix_match = re.fullmatch(r"(?:p[1-4]|blocked)-(.+)", issue_path.stem)
    return prefix_match.group(1) if prefix_match else issue_path.stem


def _resolve_issue_slug(slug: str) -> list[Path]:
    issue_dir = REPO_ROOT / "issues"
    issue_files = sorted(issue_dir.glob("*.json5"))

    exact_matches = [path for path in issue_files if path.stem == slug]
    if exact_matches:
        return exact_matches

    return [path for path in issue_files if _issue_slug(path) == slug]


def _iter_repo_source_files() -> list[Path]:
    paths = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in _CODE_EXTENSIONS:
            continue
        if any(part in _SKIP_DIR_NAMES for part in path.parts):
            continue
        paths.append(path)
    return sorted(paths)


def _validate_expiration(path: Path, line_no: int, body: str) -> str | None:
    expires_match = _EXPIRES_RE.search(body)
    if expires_match is None:
        return f"{path.relative_to(REPO_ROOT)}:{line_no}: missing expires=... metadata"

    expires = expires_match.group("expires")
    description = _EXPIRES_RE.sub("", body, count=1).strip(" -")
    if not description:
        return f"{path.relative_to(REPO_ROOT)}:{line_no}: TODO(shim) needs cleanup instructions after expires=..."

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", expires):
        expiry_date = date.fromisoformat(expires)
        if expiry_date < date.today():
            return f"{path.relative_to(REPO_ROOT)}:{line_no}: shim expired on {expiry_date.isoformat()}"
        return None

    if expires.startswith("issue:"):
        slug = expires.removeprefix("issue:")
        if not slug:
            return f"{path.relative_to(REPO_ROOT)}:{line_no}: empty issue slug in expires=issue:..."

        matches = _resolve_issue_slug(slug)
        if not matches:
            return f"{path.relative_to(REPO_ROOT)}:{line_no}: expires={expires} does not match any current issue file"
        if len(matches) > 1:
            match_list = ", ".join(str(match.relative_to(REPO_ROOT)) for match in matches)
            return (
                f"{path.relative_to(REPO_ROOT)}:{line_no}: expires={expires} matches multiple issue files: {match_list}"
            )
        return None

    return f"{path.relative_to(REPO_ROOT)}:{line_no}: unsupported expires={expires!r}; use YYYY-MM-DD or issue:<slug>"


class TestShimMetadata:
    def test_todo_shim_markers_have_valid_expiration_metadata(self) -> None:
        errors: list[str] = []

        for path in _iter_repo_source_files():
            source = path.read_text()
            for match in _SHIM_TODO_RE.finditer(source):
                line_no = source.count("\n", 0, match.start()) + 1
                error = _validate_expiration(path, line_no, match.group("body").strip())
                if error is not None:
                    errors.append(error)

        assert not errors, "Invalid TODO(shim) markers:\n  " + "\n  ".join(errors)

    def test_script_wrapper_modules_require_todo_shim_markers(self) -> None:
        missing_markers: list[str] = []

        for path in sorted((REPO_ROOT / "scripts").rglob("*.py")):
            source = path.read_text()
            header = "\n".join(source.splitlines()[:4])
            if "Compatibility wrapper for `" not in header and "CLI wrapper for `" not in header:
                continue
            if _SHIM_TODO_RE.search(source) is None:
                missing_markers.append(str(path.relative_to(REPO_ROOT)))

        assert not missing_markers, (
            "Self-identified wrapper modules must declare shim expiration metadata:\n  " + "\n  ".join(missing_markers)
        )
