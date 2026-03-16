#!/usr/bin/env python3
"""Query issues from the issues/ directory.

Usage:
    query-issues.py                          # list all with priority
    query-issues.py --label headless-client   # filter by label
    query-issues.py --max-priority 2          # P1 and P2 only
    query-issues.py --search "streaming"      # search titles and descriptions
"""

import argparse
import json
from pathlib import Path

ISSUES_DIR = Path("issues")


def main() -> None:
    parser = argparse.ArgumentParser(description="Query issues")
    parser.add_argument("--label", type=str, help="Filter by label")
    parser.add_argument(
        "--max-priority", type=int, help="Show issues with priority <= N"
    )
    parser.add_argument("--search", type=str, help="Search titles and descriptions")
    args = parser.parse_args()

    assert ISSUES_DIR.is_dir(), f"Issues directory not found: {ISSUES_DIR}"

    issues = []
    for f in sorted(ISSUES_DIR.glob("*.json")):
        data = json.loads(f.read_text())
        data["_filename"] = f.stem
        issues.append(data)

    if args.label:
        issues = [i for i in issues if args.label in i.get("labels", [])]

    if args.max_priority is not None:
        issues = [i for i in issues if i.get("priority", 999) <= args.max_priority]

    if args.search:
        term = args.search.lower()
        issues = [
            i
            for i in issues
            if term in i.get("title", "").lower()
            or term in i.get("description", "").lower()
        ]

    issues.sort(key=lambda i: i.get("priority", 999))

    for i in issues:
        print(f"{i['_filename']}: {i.get('priority', '?')}\t{i['title']}")


if __name__ == "__main__":
    main()
