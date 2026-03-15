"""Validate issue JSON files in issues/ directory."""

import json
import re
import sys
from pathlib import Path

REQUIRED_FIELDS = {
    "title",
    "description",
    "status",
    "priority",
    "type",
    "labels",
    "created_at",
    "updated_at",
}

OPTIONAL_FIELDS = {"blocked"}

KNOWN_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS
FILENAME_RE = re.compile(r"^(p[1-4]|blocked)-[a-z0-9][a-z0-9-]*$")


def _expected_filename_prefix(issue: dict) -> str:
    return "blocked" if issue.get("blocked") else f"p{issue['priority']}"


def lint_issues(project_root: Path) -> list[str]:
    issues_dir = project_root / "issues"
    if not issues_dir.exists():
        return []

    errors = []

    for issue_file in sorted(issues_dir.glob("*.json")):
        try:
            with open(issue_file) as f:
                issue = json.load(f)
        except json.JSONDecodeError as e:
            errors.append(f"{issue_file.name}: invalid JSON - {e}")
            continue

        # Filename is the id
        if "id" in issue:
            errors.append(f"{issue_file.name}: has 'id' field (filename serves as id)")

        # Check required fields
        missing = REQUIRED_FIELDS - set(issue.keys())
        if missing:
            errors.append(
                f"{issue_file.name}: missing fields: {', '.join(sorted(missing))}"
            )
            continue

        # Reject unknown fields
        unknown = set(issue.keys()) - KNOWN_FIELDS
        if unknown:
            errors.append(
                f"{issue_file.name}: unknown fields: {', '.join(sorted(unknown))}"
            )

        if not FILENAME_RE.fullmatch(issue_file.stem):
            errors.append(
                f"{issue_file.name}: filename must start with p1-/p2-/p3-/p4-/blocked- and use kebab-case"
            )
        else:
            expected_prefix = _expected_filename_prefix(issue)
            actual_prefix = issue_file.stem.split("-", 1)[0]
            if actual_prefix != expected_prefix:
                errors.append(
                    f"{issue_file.name}: filename prefix must be '{expected_prefix}-' for this issue"
                )

        # Resolved/closed issues should be deleted
        if issue["status"] != "open":
            errors.append(
                f"{issue_file.name}: status is '{issue['status']}' (delete resolved issues)"
            )

        # Priority should be 1-4
        if not isinstance(issue["priority"], int) or not 1 <= issue["priority"] <= 4:
            errors.append(
                f"{issue_file.name}: priority must be int 1-4, got {issue['priority']}"
            )

        # Labels should be a list
        if not isinstance(issue["labels"], list):
            errors.append(f"{issue_file.name}: labels must be an array")

        if "blocked" in issue and not isinstance(issue["blocked"], (bool, str)):
            errors.append(f"{issue_file.name}: blocked must be a boolean or string")

    return errors


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent.parent
    errors = lint_issues(project_root)

    if errors:
        print("Issue validation errors:", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        sys.exit(1)

    print("Issues: OK")


if __name__ == "__main__":
    main()
