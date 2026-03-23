"""Convention tests for harness epoch history and golden coherence."""

import ast
import re
import subprocess

import pytest

from tests.weird.repo_convention_helpers import REPO_ROOT, changed_files_since_master


class TestHarnessEpochMonotonic:
    def test_epoch_matches_history(self) -> None:
        source = (REPO_ROOT / "src" / "magebench" / "game" / "harness_epoch.py").read_text()

        tree = ast.parse(source)
        epoch_value = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "HARNESS_EPOCH"
                and isinstance(node.value, ast.Constant)
            ):
                epoch_value = node.value.value
        assert isinstance(epoch_value, int), f"HARNESS_EPOCH must be an int, got {type(epoch_value)}"

        history_epochs = [int(match) for match in re.findall(r"#\s+(\d+)\s+-\s+", source)]
        assert history_epochs, "No history comments found in harness_epoch.py"

        assert epoch_value == max(history_epochs), (
            f"HARNESS_EPOCH={epoch_value} doesn't match max history entry {max(history_epochs)}"
        )

        expected = list(range(1, max(history_epochs) + 1))
        assert sorted(history_epochs) == expected, f"History has gaps or duplicates: {sorted(history_epochs)}"


class TestGoldenEpochCoherence:
    """Two-way invariant between golden output and harness epoch.

    1. Modified existing golden output -> harness epoch must be bumped.
    2. Bumped harness epoch -> all goldens must be regenerated.
    """

    _EXPORT_GOLDEN_PREFIX = "tests/golden/exports/"
    _EPOCH_FILE = "src/magebench/game/harness_epoch.py"

    def test_golden_changes_require_epoch_bump(self) -> None:
        """If existing export golden output changed, HARNESS_EPOCH must be bumped too."""
        changed = changed_files_since_master()
        if changed is None:
            pytest.skip("On master or git unavailable")

        merge_base = subprocess.run(
            ["git", "merge-base", "master", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        result = subprocess.run(
            [
                "git",
                "diff",
                "--diff-filter=M",
                "--name-only",
                merge_base,
                "--",
                self._EXPORT_GOLDEN_PREFIX,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        modified_goldens = set(result.stdout.strip().splitlines()) if result.stdout.strip() else set()
        if not modified_goldens:
            return

        assert self._EPOCH_FILE in changed, (
            f"{len(modified_goldens)} export golden(s) modified without bumping HARNESS_EPOCH.\n"
            "Export golden output changes mean the harness changed — bump the epoch.\n"
            "Modified goldens:\n  " + "\n  ".join(sorted(modified_goldens))
        )

    def test_epoch_bump_requires_full_regen(self) -> None:
        """If HARNESS_EPOCH was bumped, all export goldens must be regenerated.

        Export goldens embed harnessEpoch, so they always change when the epoch
        bumps. If any export golden is untouched, ``make regen-golden`` was not
        run. (Prompt/blunder goldens may legitimately be unchanged if the epoch
        bump didn't affect prompt content.)
        """
        changed = changed_files_since_master()
        if changed is None:
            pytest.skip("On master or git unavailable")

        if self._EPOCH_FILE not in changed:
            return

        exports_dir = REPO_ROOT / "tests" / "golden" / "exports"
        all_exports = {str(path.relative_to(REPO_ROOT)) for path in exports_dir.glob("*.json5")}

        untouched = all_exports - changed
        assert not untouched, (
            f"HARNESS_EPOCH was bumped but {len(untouched)} export golden(s) not regenerated.\n"
            "Run `make regen-golden` after bumping the epoch.\n"
            "Untouched exports:\n  " + "\n  ".join(sorted(untouched))
        )
