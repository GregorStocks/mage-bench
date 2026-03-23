"""Tests for the ground-truth migration helper."""

from pathlib import Path

import magebench.analysis.toolbox.migrate_ground_truth as migrate_ground_truth


def test_ground_truth_dir_uses_blunder_package_data() -> None:
    toolbox_dir = Path(migrate_ground_truth.__file__).resolve().parent
    ground_truth_dir = migrate_ground_truth.GROUND_TRUTH_DIR
    expected_ground_truth_dir = toolbox_dir.parent / "blunder" / "ground_truth"

    assert ground_truth_dir == expected_ground_truth_dir
