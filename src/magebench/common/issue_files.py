from pathlib import Path
from typing import Any

from magebench.common.json5_utils import loads_json5

ISSUE_SUFFIX = ".json5"


def issue_stem(issue_name: str) -> str:
    return issue_name.removesuffix(ISSUE_SUFFIX)


def issue_path(issues_dir: Path, issue_name: str) -> Path:
    return issues_dir / f"{issue_stem(issue_name)}{ISSUE_SUFFIX}"


def iter_issue_files(issues_dir: Path) -> list[Path]:
    return sorted(issues_dir.glob(f"*{ISSUE_SUFFIX}"))


def load_issue(path: Path) -> dict[str, Any]:
    data = loads_json5(path.read_text())
    assert isinstance(data, dict), f"Issue file must contain an object: {path}"
    return data
