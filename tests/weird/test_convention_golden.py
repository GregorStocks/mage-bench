"""Convention tests for golden test naming and markers."""

from typing import ClassVar

from tests.weird.repo_convention_helpers import REPO_ROOT


class TestGoldenFilesHaveMarker:
    # Infrastructure tests that test golden helpers/timing, not actual
    # golden integration tests. These don't need the XMage server.
    _INFRA_FILES: ClassVar[set[str]] = {
        "test_golden_helpers_health.py",
        "test_golden_helpers_normalization.py",
        "test_golden_test_identities.py",
        "test_golden_timing.py",
    }

    def test_golden_naming_implies_marker(self) -> None:
        tests_dir = REPO_ROOT / "tests"
        golden_files = sorted(tests_dir.glob("test_golden_*.py"))
        assert golden_files, "No test_golden_*.py files found"

        missing_marker = []
        for path in golden_files:
            if path.name in self._INFRA_FILES:
                continue
            source = path.read_text()
            if "@golden_test(" not in source:
                missing_marker.append(path.name)

        assert not missing_marker, "Golden test files without @golden_test(...):\n  " + "\n  ".join(missing_marker)

    def test_infra_files_exist(self) -> None:
        """Ensure the infra allowlist doesn't reference deleted files."""
        tests_dir = REPO_ROOT / "tests"
        for name in self._INFRA_FILES:
            assert (tests_dir / name).exists(), f"{name} is in _INFRA_FILES allowlist but doesn't exist — remove it"
