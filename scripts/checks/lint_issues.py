"""Validate issue JSON files in issues/ directory."""

import json
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

OPTIONAL_FIELDS = {"not_autoclaimable"}

KNOWN_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS


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
