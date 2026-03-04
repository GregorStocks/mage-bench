#!/usr/bin/env python3
"""Promote a blunder eval result to the checked-in baseline.

Without args: promotes the most recent tmp/blunder_eval_*.json.
With arg: promotes the specified file.

Usage:
    uv run --project puppeteer python scripts/analysis/blunder_promote.py [PATH]
"""

import json
from pathlib import Path

from scripts.analysis.blunder_eval_common import BASELINE_PATH, TMP_DIR, save_baseline


def find_latest_eval() -> Path:
    """Find the most recent eval file in tmp/."""
    candidates = sorted(TMP_DIR.glob("blunder_eval_*.json"))
    assert candidates, f"No blunder_eval_*.json files found in {TMP_DIR}"
    return candidates[-1]


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Promote eval results to baseline")
    parser.add_argument(
        "path", nargs="?", help="Path to eval file (default: most recent)"
    )
    args = parser.parse_args()

    if args.path:
        eval_path = Path(args.path)
    else:
        eval_path = find_latest_eval()

    assert eval_path.exists(), f"Eval file not found: {eval_path}"

    data = json.loads(eval_path.read_text())

    assert "results" in data, f"Invalid eval file (no 'results' key): {eval_path}"
    assert "blunder_script_version" in data, (
        f"Invalid eval file (no 'blunder_script_version' key): {eval_path}"
    )

    save_baseline(data)

    n = len(data["results"])
    v = data["blunder_script_version"]
    print(f"Promoted {eval_path.name} -> {BASELINE_PATH.name}")
    print(f"  Version: v{v}")
    print(f"  Results: {n}")


if __name__ == "__main__":
    main()
