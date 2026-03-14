#!/usr/bin/env python3
"""Extract the first valid top-level JSON array from stdin."""

from __future__ import annotations

import json
import sys


def main() -> int:
    lines = sys.stdin.read().splitlines(keepends=True)
    start_indexes = [i for i, line in enumerate(lines) if line.strip() == "["]
    end_indexes = [i for i, line in enumerate(lines) if line.strip() == "]"]

    for start in start_indexes:
        for end in reversed(end_indexes):
            if end < start:
                continue
            candidate = "".join(lines[start : end + 1])
            try:
                json.loads(candidate)
            except json.JSONDecodeError:
                continue
            sys.stdout.write(candidate)
            return 0

    raise SystemExit("no valid top-level JSON array found in stdin")


if __name__ == "__main__":
    raise SystemExit(main())
