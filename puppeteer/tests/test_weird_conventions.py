"""Weird tests: automated enforcement of repo conventions.

These are "regressions for things that feel silly flagging in code review"
(https://www.jmduke.com/posts/weird-tests-2.html). Each test encodes a
convention that's easy to violate and tedious to police manually.
"""

import ast
import gzip
import json
import re
from pathlib import Path
from typing import Any, ClassVar

import jsonschema
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PUPPETEER_DIR = REPO_ROOT / "puppeteer"
GAMES_DIR = REPO_ROOT / "website" / "public" / "games"
SCHEMA_PATH = REPO_ROOT / "schemas" / "game-export-v2.schema.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _load_game(path: Path) -> dict:
    if path.suffix == ".gz":
        with gzip.open(path, "rt") as f:
            return json.load(f)
    return json.loads(path.read_text())


def _glob_game_files() -> list[Path]:
    gz_files = set(GAMES_DIR.glob("game_*.json.gz"))
    gz_stems = {p.name.removesuffix(".gz") for p in gz_files}
    json_files = [p for p in GAMES_DIR.glob("game_*.json") if p.name not in gz_stems]
    return sorted(gz_files | set(json_files))


# ---------------------------------------------------------------------------
# Test 2: Every preset references a model that exists
# ---------------------------------------------------------------------------


class TestPresetsReferenceValidModels:
    def test_all_preset_models_exist(self) -> None:
        models_data = _load_json(PUPPETEER_DIR / "models.json")
        model_ids = {m["id"] for m in models_data["models"]}
        presets_data = _load_json(PUPPETEER_DIR / "presets.json")

        missing = []
        for name, preset in presets_data["presets"].items():
            if preset["model"] not in model_ids:
                missing.append(f"{name!r} -> {preset['model']!r}")

        assert not missing, "Presets reference unknown models:\n  " + "\n  ".join(missing)

    def test_gauntlet_entries_exist_in_presets(self) -> None:
        presets_data = _load_json(PUPPETEER_DIR / "presets.json")
        preset_names = set(presets_data["presets"])
        gauntlet = presets_data["gauntlet"]

        missing = [g for g in gauntlet if g not in preset_names]
        assert not missing, f"Gauntlet references unknown presets: {missing}"

    def test_preset_system_prompts_exist(self) -> None:
        presets_data = _load_json(PUPPETEER_DIR / "presets.json")

        # Collect available prompt keys from prompts/ dir and prompts.json
        prompt_keys: set[str] = set()
        prompts_dir = PUPPETEER_DIR / "prompts"
        if prompts_dir.is_dir():
            for md in prompts_dir.glob("*.md"):
                prompt_keys.add(md.stem)
        prompts_json = PUPPETEER_DIR / "prompts.json"
        if prompts_json.exists():
            prompt_keys.update(_load_json(prompts_json).keys())

        missing = []
        for name, preset in presets_data["presets"].items():
            sp = preset.get("system_prompt")
            if sp and sp not in prompt_keys:
                missing.append(f"{name!r} -> {sp!r}")

        assert not missing, "Presets reference unknown system_prompts:\n  " + "\n  ".join(missing)


# ---------------------------------------------------------------------------
# Test 3: Every toolset references tools that actually exist
# ---------------------------------------------------------------------------


class TestToolsetsReferenceValidTools:
    def test_all_toolset_tools_exist(self) -> None:
        toolsets = _load_json(PUPPETEER_DIR / "toolsets.json")
        mcp_tools = _load_json(REPO_ROOT / "website" / "src" / "data" / "mcp-tools.json")
        real_tool_names = {t["name"] for t in mcp_tools}

        missing = []
        for toolset_name, tools in toolsets.items():
            for tool in tools:
                if tool not in real_tool_names:
                    missing.append(f"{toolset_name!r} -> {tool!r}")

        assert not missing, "Toolsets reference nonexistent MCP tools:\n  " + "\n  ".join(missing)

    def test_preset_toolsets_exist(self) -> None:
        presets_data = _load_json(PUPPETEER_DIR / "presets.json")
        toolsets = _load_json(PUPPETEER_DIR / "toolsets.json")

        missing = []
        for name, preset in presets_data["presets"].items():
            ts = preset.get("toolset")
            if ts and ts not in toolsets:
                missing.append(f"{name!r} -> {ts!r}")

        assert not missing, "Presets reference unknown toolsets:\n  " + "\n  ".join(missing)


# ---------------------------------------------------------------------------
# Test 5: Harness epoch never decreases
# ---------------------------------------------------------------------------


class TestHarnessEpochMonotonic:
    def test_epoch_matches_history(self) -> None:
        source = (REPO_ROOT / "puppeteer" / "src" / "puppeteer" / "harness_epoch.py").read_text()

        # Extract HARNESS_EPOCH assignment
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

        # Extract all epoch numbers from history comments (e.g. "#   7 - ...")
        history_epochs = [int(m) for m in re.findall(r"#\s+(\d+)\s+-\s+", source)]
        assert history_epochs, "No history comments found in harness_epoch.py"

        # Current epoch must equal the max in history
        assert epoch_value == max(history_epochs), (
            f"HARNESS_EPOCH={epoch_value} doesn't match max history entry {max(history_epochs)}"
        )

        # History must be contiguous 1..N
        expected = list(range(1, max(history_epochs) + 1))
        assert sorted(history_epochs) == expected, f"History has gaps or duplicates: {sorted(history_epochs)}"


# ---------------------------------------------------------------------------
# Test 6: Every exported game validates against the schema
# ---------------------------------------------------------------------------


class TestAllExportsValid:
    @pytest.mark.parametrize(
        "game_file",
        _glob_game_files(),
        ids=lambda p: p.name,
    )
    def test_game_conforms_to_schema(self, game_file: Path) -> None:
        schema = _load_json(SCHEMA_PATH)
        validator = jsonschema.Draft7Validator(schema)
        data = _load_game(game_file)
        errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
        assert not errors, f"{errors[0].message} (at {'/'.join(str(p) for p in errors[0].absolute_path)})"


# ---------------------------------------------------------------------------
# Test 7: No orphaned deck files
# ---------------------------------------------------------------------------


class TestNoOrphanedDecks:
    def test_all_decks_referenced_by_registry(self) -> None:
        """Every deck JSON in data/decks/{format}/ should exist and be loadable.

        Since the registry IS the deck files (each .json file is a
        self-contained deck definition), this test ensures no deck file is
        broken or empty — an orphan would be a file that isn't valid JSON
        with the required fields.
        """
        data_dir = REPO_ROOT / "data" / "decks"
        format_dirs = ["standard", "modern", "legacy", "commander"]

        for fmt in format_dirs:
            fmt_dir = data_dir / fmt
            if not fmt_dir.is_dir():
                continue
            deck_files = list(fmt_dir.glob("*.json"))
            assert deck_files, f"No deck files in {fmt}/"
            for f in deck_files:
                data = json.loads(f.read_text())
                assert "name" in data, f"{fmt}/{f.name} missing 'name'"
                assert "cards" in data, f"{fmt}/{f.name} missing 'cards'"
                assert data["cards"], f"{fmt}/{f.name} has empty cards list"


# ---------------------------------------------------------------------------
# Test 8: All config files load without error
# ---------------------------------------------------------------------------


class TestAllConfigsLoad:
    @pytest.mark.parametrize(
        "config_path",
        sorted((REPO_ROOT / "configs").glob("*.json")),
        ids=lambda p: p.name,
    )
    def test_config_parses(self, config_path: Path) -> None:
        """Every config file must be valid JSON with a players array."""
        data = json.loads(config_path.read_text())
        assert "players" in data, f"{config_path.name} missing 'players'"
        assert isinstance(data["players"], list), f"{config_path.name} players is not a list"
        assert data["players"], f"{config_path.name} has empty players list"


# ---------------------------------------------------------------------------
# Test 9: Golden test files all have the @pytest.mark.golden marker
# ---------------------------------------------------------------------------


class TestGoldenFilesHaveMarker:
    # Infrastructure tests that test golden helpers/timing, not actual
    # golden integration tests.  These don't need the XMage server.
    _INFRA_FILES: ClassVar[set[str]] = {
        "test_golden_helpers_health.py",
        "test_golden_helpers_normalization.py",
        "test_golden_timing.py",
    }

    def test_golden_naming_implies_marker(self) -> None:
        tests_dir = REPO_ROOT / "puppeteer" / "tests"
        golden_files = sorted(tests_dir.glob("test_golden_*.py"))
        assert golden_files, "No test_golden_*.py files found"

        missing_marker = []
        for path in golden_files:
            if path.name in self._INFRA_FILES:
                continue
            source = path.read_text()
            if "pytest.mark.golden" not in source:
                missing_marker.append(path.name)

        assert not missing_marker, "Golden test files without @pytest.mark.golden:\n  " + "\n  ".join(missing_marker)

    def test_infra_files_exist(self) -> None:
        """Ensure the infra allowlist doesn't reference deleted files."""
        tests_dir = REPO_ROOT / "puppeteer" / "tests"
        for name in self._INFRA_FILES:
            assert (tests_dir / name).exists(), f"{name} is in _INFRA_FILES allowlist but doesn't exist — remove it"


# ---------------------------------------------------------------------------
# Test 10: name_part values in models.json are unique
# ---------------------------------------------------------------------------


class TestModelNamePartsUnique:
    def test_no_duplicate_name_parts(self) -> None:
        models_data = _load_json(PUPPETEER_DIR / "models.json")

        seen: dict[str, list[str]] = {}
        for model in models_data["models"]:
            seen.setdefault(model["name_part"], []).append(model["id"])

        dupes = {np: ids for np, ids in seen.items() if len(ids) > 1}
        assert not dupes, "Duplicate name_parts (would be ambiguous on leaderboard):\n  " + "\n  ".join(
            f"{np!r}: {ids}" for np, ids in dupes.items()
        )
