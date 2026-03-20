"""Weird tests: automated enforcement of repo conventions.

These are "regressions for things that feel silly flagging in code review"
(https://www.jmduke.com/posts/weird-tests-2.html). Each test encodes a
convention that's easy to violate and tedious to police manually.
"""

import ast
import gzip
import json
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

import pytest

from scripts.json5_utils import loads_json5

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PUPPETEER_DIR = REPO_ROOT / "puppeteer"
GAMES_DIR = REPO_ROOT / "website" / "public" / "games"
DECKS_DIR = REPO_ROOT / "data" / "decks"
CONFIGS_DIR = REPO_ROOT / "configs"

# Special preset/personality keywords resolved at runtime, not looked up in JSON.
_SPECIAL_PRESET_KEYWORDS = {"random", "round-robin"}
_SPECIAL_PERSONALITY_KEYWORDS = {"random"}

# Models that were retired from models.json but still appear in historical
# exported games.  Add entries here when removing a model.
_RETIRED_MODELS: set[str] = {
    "mistralai/devstral-small",
}

# The canonical set of deck format directories under data/decks/.
_EXPECTED_DECK_FORMATS = {"standard", "modern", "legacy", "commander", "jumpstart"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> object:
    return json.loads(path.read_text())


def _load_game_file(path: Path) -> dict:
    """Load a game export file (plain JSON5 or gzipped)."""
    if path.suffix == ".gz":
        with gzip.open(path, "rt") as f:
            return loads_json5(f.read())
    with open(path) as f:
        return loads_json5(f.read())


def _all_game_files() -> list[Path]:
    gz_files = set(GAMES_DIR.glob("game_*.json5.gz"))
    gz_stems = {p.name.removesuffix(".gz") for p in gz_files}
    json_files = [p for p in GAMES_DIR.glob("game_*.json5") if p.name not in gz_stems]
    return sorted(gz_files | set(json_files))


def _changed_files_since_master() -> set[str] | None:
    """Return repo-relative paths changed since master, or None if on master / git fails."""
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if branch == "master":
            return None

        merge_base = subprocess.run(
            ["git", "merge-base", "master", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        diff_result = subprocess.run(
            ["git", "diff", "--name-only", merge_base],
            capture_output=True,
            text=True,
            check=True,
        )
        untracked_result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            check=True,
        )

        changed = set(diff_result.stdout.strip().splitlines())
        changed.update(untracked_result.stdout.strip().splitlines())
        return changed
    except subprocess.CalledProcessError:
        return None


def _changed_game_filenames() -> set[str] | None:
    """Return filenames of game exports changed since master, or None for all.

    Returns None (= validate everything) when on master, when the export
    script or schema changed, or when git commands fail.
    """
    changed = _changed_files_since_master()
    if changed is None:
        return None

    # If export script or schema changed, validate everything
    schema_files = {f for f in changed if f.startswith("schemas/game-export-v") and f.endswith(".schema.json")}
    if schema_files or "scripts/export_game.py" in changed:
        return None

    prefix = "website/public/games/"
    return {f.removeprefix(prefix) for f in changed if f.startswith(prefix)}


def _glob_game_files() -> list[Path]:
    """Game export files to validate.

    By default only files changed since master are returned.
    Set CHECK_ALL_EXPORTS=1 to validate every export.
    """
    all_files = _all_game_files()

    if os.environ.get("CHECK_ALL_EXPORTS") == "1":
        return all_files

    changed = _changed_game_filenames()
    if changed is None:
        return all_files

    return [f for f in all_files if f.name in changed]


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

    def test_all_presets_have_valid_status(self) -> None:
        presets_data = _load_json(PUPPETEER_DIR / "presets.json")
        valid_statuses = {"active", "retired", "buggy", "expensive"}

        bad = []
        for name, preset in presets_data["presets"].items():
            status = preset.get("status")
            if status is None:
                bad.append(f"{name!r}: missing 'status' field")
            elif status not in valid_statuses:
                bad.append(f"{name!r}: invalid status {status!r}")

        assert not bad, "Preset status issues:\n  " + "\n  ".join(bad)

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
        mcp_tools = loads_json5((REPO_ROOT / "website" / "src" / "data" / "mcp-tools.json5").read_text())
        real_tool_names = {t["name"] for t in mcp_tools}

        missing = []
        for toolset_name, tools in toolsets.items():
            missing.extend(f"{toolset_name!r} -> {tool!r}" for tool in tools if tool not in real_tool_names)

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
    def test_game_conforms_to_schema(
        self, game_file: Path, all_games_data: Mapping[Path, dict], game_export_validator
    ) -> None:
        data = all_games_data[game_file]
        version = data["version"]
        assert version in game_export_validator, f"No schema for version {version}"
        validator = game_export_validator[version]
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
# Test 9: Real golden test files all use the @golden_test decorator
# ---------------------------------------------------------------------------


class TestGoldenFilesHaveMarker:
    # Infrastructure tests that test golden helpers/timing, not actual
    # golden integration tests.  These don't need the XMage server.
    _INFRA_FILES: ClassVar[set[str]] = {
        "test_golden_helpers_health.py",
        "test_golden_helpers_normalization.py",
        "test_golden_test_identities.py",
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
            if "@golden_test(" not in source:
                missing_marker.append(path.name)

        assert not missing_marker, "Golden test files without @golden_test(...):\n  " + "\n  ".join(missing_marker)

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


# ---------------------------------------------------------------------------
# Test 11: Personality name_parts are unique
# ---------------------------------------------------------------------------


class TestPersonalityNamePartsUnique:
    def test_no_duplicate_name_parts(self) -> None:
        personalities = _load_json(PUPPETEER_DIR / "personalities.json")

        seen: dict[str, list[str]] = {}
        for key, val in personalities.items():
            seen.setdefault(val["name_part"], []).append(key)

        dupes = {np: keys for np, keys in seen.items() if len(keys) > 1}
        assert not dupes, "Duplicate personality name_parts:\n  " + "\n  ".join(
            f"{np!r}: {keys}" for np, keys in dupes.items()
        )


# ---------------------------------------------------------------------------
# Test 12: Config presets and personalities reference valid values
# ---------------------------------------------------------------------------


class TestConfigReferencesValid:
    def test_config_presets_are_valid(self) -> None:
        """Every preset in a config player must be a special keyword or exist in presets.json."""
        presets_data = _load_json(PUPPETEER_DIR / "presets.json")
        preset_names = set(presets_data["presets"])

        bad = []
        for config_path in sorted(CONFIGS_DIR.glob("*.json")):
            data = _load_json(config_path)
            for i, player in enumerate(data.get("players", [])):
                preset = player.get("preset")
                if preset and preset not in _SPECIAL_PRESET_KEYWORDS and preset not in preset_names:
                    bad.append(f"{config_path.name} player[{i}]: {preset!r}")

        assert not bad, "Config players reference unknown presets:\n  " + "\n  ".join(bad)

    def test_config_personalities_are_valid(self) -> None:
        """Every personality in a config player must be a special keyword or exist in personalities.json."""
        personalities = _load_json(PUPPETEER_DIR / "personalities.json")
        personality_names = set(personalities)

        bad = []
        for config_path in sorted(CONFIGS_DIR.glob("*.json")):
            data = _load_json(config_path)
            for i, player in enumerate(data.get("players", [])):
                personality = player.get("personality")
                if (
                    personality
                    and personality not in _SPECIAL_PERSONALITY_KEYWORDS
                    and personality not in personality_names
                ):
                    bad.append(f"{config_path.name} player[{i}]: {personality!r}")

        assert not bad, "Config players reference unknown personalities:\n  " + "\n  ".join(bad)


# ---------------------------------------------------------------------------
# Test 13: Exported games only reference known models
# ---------------------------------------------------------------------------


class TestExportedGameModelsKnown:
    def test_game_models_exist(self, all_games_data: Mapping[Path, dict]) -> None:
        """Every player.model in exported games must be in models.json or the retired allowlist."""
        models_data = _load_json(PUPPETEER_DIR / "models.json")
        model_ids = {m["id"] for m in models_data["models"]}
        allowed = model_ids | _RETIRED_MODELS

        # On feature branches, only check changed games — unless models.json changed
        changed = _changed_files_since_master()
        if changed is not None and "puppeteer/models.json" not in changed:
            game_files = _glob_game_files()
        else:
            game_files = list(all_games_data.keys())

        unknown: list[str] = []
        for game_file in game_files:
            data = all_games_data[game_file]
            for player in data.get("players", []):
                model = player.get("model")
                if model and model not in allowed:
                    unknown.append(f"{game_file.name}: {model!r}")

        assert not unknown, (
            "Exported games reference unknown models (add to _RETIRED_MODELS if intentional):\n  "
            + "\n  ".join(unknown)
        )


# ---------------------------------------------------------------------------
# Test 14: No orphaned prompt files
# ---------------------------------------------------------------------------


class TestNoOrphanedPrompts:
    def test_all_prompts_referenced_by_presets(self) -> None:
        """Every .md file in prompts/ should be referenced by at least one preset."""
        prompts_dir = PUPPETEER_DIR / "prompts"
        if not prompts_dir.is_dir():
            return
        prompt_files = {md.stem for md in prompts_dir.glob("*.md")}
        if not prompt_files:
            return

        presets_data = _load_json(PUPPETEER_DIR / "presets.json")
        referenced = {
            preset.get("system_prompt") for preset in presets_data["presets"].values() if preset.get("system_prompt")
        }

        orphaned = prompt_files - referenced
        assert not orphaned, "Prompt files not referenced by any preset:\n  " + "\n  ".join(
            f"{name}.md" for name in sorted(orphaned)
        )


# ---------------------------------------------------------------------------
# Test 15: Deck format directories match expected set
# ---------------------------------------------------------------------------


class TestDeckFormatDirectories:
    def test_no_unexpected_format_dirs(self) -> None:
        """Subdirectories under data/decks/ must be in the expected set — catches typos like 'standrard'."""
        actual = {d.name for d in DECKS_DIR.iterdir() if d.is_dir()}
        unexpected = actual - _EXPECTED_DECK_FORMATS
        assert not unexpected, (
            f"Unexpected deck format directories (typo?): {sorted(unexpected)}. "
            f"If intentional, add to _EXPECTED_DECK_FORMATS."
        )

    def test_all_expected_formats_exist(self) -> None:
        """Every expected format directory should exist and contain decks."""
        for fmt in sorted(_EXPECTED_DECK_FORMATS):
            fmt_dir = DECKS_DIR / fmt
            assert fmt_dir.is_dir(), f"Expected deck format directory missing: {fmt}/"


# ---------------------------------------------------------------------------
# Test 16: Config deckType uses recognized format keywords
# ---------------------------------------------------------------------------


class TestConfigDeckTypes:
    _VALID_DECK_TYPES: ClassVar[set[str]] = {
        "Constructed - Standard",
        "Constructed - Modern",
        "Constructed - Legacy",
        "Limited",
        "Variant Magic - Freeform Commander",
        "Variant Magic - Commander",
    }

    def test_deck_types_recognized(self) -> None:
        """Every deckType value in configs must be a known XMage deck type."""
        bad = []
        for config_path in sorted(CONFIGS_DIR.glob("*.json")):
            data = _load_json(config_path)
            deck_type = data.get("deckType")
            if deck_type is None:
                continue
            # deckType can be a string or a list of strings
            types = deck_type if isinstance(deck_type, list) else [deck_type]
            bad.extend(f"{config_path.name}: {dt!r}" for dt in types if dt not in self._VALID_DECK_TYPES)

        assert not bad, (
            "Configs use unrecognized deckType values (add to _VALID_DECK_TYPES if intentional):\n  " + "\n  ".join(bad)
        )


# ---------------------------------------------------------------------------
# Test 17: Personality name_part length is bounded
# ---------------------------------------------------------------------------


class TestPersonalityNamePartLength:
    # XMage player names have a length limit.  Model name_part + " " +
    # personality name_part must fit.  Max model name_part is currently 7
    # chars; keeping personality name_part <= 7 leaves room.
    _MAX_LENGTH = 7

    def test_name_parts_not_too_long(self) -> None:
        personalities = _load_json(PUPPETEER_DIR / "personalities.json")

        too_long = [
            f"{key!r}: {val['name_part']!r} ({len(val['name_part'])} chars)"
            for key, val in personalities.items()
            if len(val["name_part"]) > self._MAX_LENGTH
        ]
        assert not too_long, f"Personality name_parts exceed {self._MAX_LENGTH} chars:\n  " + "\n  ".join(too_long)


# ---------------------------------------------------------------------------
# Test 18: At least one active preset exists
# ---------------------------------------------------------------------------


class TestActivePresetsExist:
    def test_has_active_presets(self) -> None:
        presets_data = _load_json(PUPPETEER_DIR / "presets.json")
        active = [name for name, p in presets_data["presets"].items() if p.get("status") == "active"]
        assert active, "No presets with status='active' — matchmaking needs at least one"


# ---------------------------------------------------------------------------
# Test 19: Golden output and harness epoch must move together
# ---------------------------------------------------------------------------


class TestGoldenEpochCoherence:
    """Two-way invariant between golden output and harness epoch.

    1. Modified existing golden output → harness epoch must be bumped.
    2. Bumped harness epoch → all goldens must be regenerated.
    """

    # Only export goldens (game replay output) require an epoch bump when
    # modified.  Blunder prompt goldens test post-game analysis prompts, not
    # the harness itself, so changing them doesn't affect game comparability.
    _EXPORT_GOLDEN_PREFIX = "puppeteer/tests/golden/exports/"
    _EPOCH_FILE = "puppeteer/src/puppeteer/harness_epoch.py"

    def test_golden_changes_require_epoch_bump(self) -> None:
        """If existing export golden output changed, HARNESS_EPOCH must be bumped too."""
        changed = _changed_files_since_master()
        if changed is None:
            pytest.skip("On master or git unavailable")

        # Only *modified* (not added) golden files — new tests don't require
        # an epoch bump, only changes to existing golden output do.
        merge_base = subprocess.run(
            ["git", "merge-base", "master", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        result = subprocess.run(
            ["git", "diff", "--diff-filter=M", "--name-only", merge_base, "--", self._EXPORT_GOLDEN_PREFIX],
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
        bumps.  If any export golden is untouched, ``make regen-golden`` was not
        run.  (Prompt/blunder goldens may legitimately be unchanged if the epoch
        bump didn't affect prompt content.)
        """
        changed = _changed_files_since_master()
        if changed is None:
            pytest.skip("On master or git unavailable")

        if self._EPOCH_FILE not in changed:
            return

        exports_dir = REPO_ROOT / "puppeteer" / "tests" / "golden" / "exports"
        all_exports = {str(p.relative_to(REPO_ROOT)) for p in exports_dir.glob("*.json")}

        untouched = all_exports - changed
        assert not untouched, (
            f"HARNESS_EPOCH was bumped but {len(untouched)} export golden(s) not regenerated.\n"
            "Run `make regen-golden` after bumping the epoch.\n"
            "Untouched exports:\n  " + "\n  ".join(sorted(untouched))
        )
