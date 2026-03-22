#!/usr/bin/env python3
"""One-shot migration: slim down ground truth entries.

Unaudited entries (verdict=null) -> {"decision_index": N}
Audited entries (verdict!=null) -> 6-field format with annotation_version

Usage:
    uv run python -m magebench.analysis.toolbox.migrate_ground_truth
"""

import json
import re
from pathlib import Path

GROUND_TRUTH_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "scripts"
    / "analysis"
    / "ground_truth"
)


def extract_version(source: str | None) -> int:
    """Extract version number from source field.

    "annotation_v11" -> 11
    "manual" -> 0 (no annotator version)
    None -> 0
    """
    if source and source.startswith("annotation_v"):
        m = re.match(r"annotation_v(\d+)", source)
        if m:
            return int(m.group(1))
    return 0


def migrate_entry(entry: dict) -> dict:
    """Migrate a single entry to slim format."""
    verdict = entry.get("verdict")
    if verdict is None:
        # Unaudited: just a pointer
        return {"decision_index": entry["decision_index"]}
    # Audited: keep annotation info + verdict
    return {
        "decision_index": entry["decision_index"],
        "annotation_version": extract_version(entry.get("source")),
        "annotation_severity": entry.get("annotation_severity"),
        "annotation_description": entry.get("annotation_description"),
        "verdict": verdict,
        "human_notes": entry.get("human_notes"),
    }


def main() -> None:
    files = sorted(GROUND_TRUTH_DIR.glob("*.json"))
    assert files, f"No ground truth files found in {GROUND_TRUTH_DIR}"

    total_entries = 0
    total_audited = 0
    total_unaudited = 0
    files_modified = 0

    for path in files:
        with open(path) as f:
            entries = json.load(f)

        migrated = [migrate_entry(e) for e in entries]
        total_entries += len(migrated)

        audited = [e for e in migrated if "verdict" in e]
        unaudited = [e for e in migrated if "verdict" not in e]
        total_audited += len(audited)
        total_unaudited += len(unaudited)

        # Sort by decision_index and write back
        migrated.sort(key=lambda e: e["decision_index"])
        path.write_text(json.dumps(migrated, indent=2) + "\n")
        files_modified += 1

    print(f"Migrated {files_modified} files, {total_entries} entries")
    print(f"  Audited: {total_audited}")
    print(f"  Unaudited: {total_unaudited}")

    # Validate audited entries
    for path in files:
        with open(path) as f:
            entries = json.load(f)
        for e in entries:
            if "verdict" in e:
                assert "annotation_version" in e, (
                    f"Missing annotation_version in {path}: {e}"
                )
                assert "annotation_severity" in e, (
                    f"Missing annotation_severity in {path}: {e}"
                )
                assert "annotation_description" in e, (
                    f"Missing annotation_description in {path}: {e}"
                )
                assert "human_notes" in e, f"Missing human_notes in {path}: {e}"
            else:
                assert list(e.keys()) == ["decision_index"], (
                    f"Unexpected keys in unaudited entry {path}: {e}"
                )

    print("Validation passed.")


if __name__ == "__main__":
    main()
