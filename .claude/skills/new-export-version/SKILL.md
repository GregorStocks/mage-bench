---
name: new-export-version
description: Create a new game export schema version, migration module, and related tests.
---

# New Export Version

Cut a new game export format version. This creates a standalone schema, migration module, and updates all references.

## Background

Each export version has its own schema file (`src/magebench/game/game-export-vN.schema.json`). In this repo, export migrations live in the single module `src/magebench/game/game_export_migrations.py`, and raw export file I/O lives in `src/magebench/game/game_exports.py`.

Key files:

- `src/magebench/game/game-export-v*.schema.json` — per-version JSON Schemas
- `src/magebench/game/game_export_migrations.py` — current/legacy version constants and normalization helpers
- `src/magebench/game/game_exports.py` — raw export load/write helpers used by scripts and tests
- `src/magebench/game/export_game.py` — export producer (sets version, computes new fields)
- `tests/test_game_export_migrations.py` — roundtrip migration tests
- `tests/test_export_schema.py` — schema validation tests

## Step 1: Determine what's changing

Ask the user what fields are being added, removed, or modified. Determine:

- The current version number N (check the `"version"` line in `src/magebench/game/export_game.py`)
- What new fields to add and their JSON Schema types
- Whether the `up()` migration needs external data or is purely derived from existing fields
- Whether the `down()` migration is lossless (can we reconstruct N from N+1?)

Before deciding on a version bump for a semantics-only fix, check whether the
latest typed loaders (`load_game_export`, `load_built_game_export`) are used
directly against committed exports in tests or scripts. If they only accept the
newest version, a code-only version bump can break local consumers until the
repo's game exports are migrated too. In that case, consider whether an
in-place backfill on the existing version is the safer path.

## Step 2: Create the new schema file

Copy `src/magebench/game/game-export-vN.schema.json` to `src/magebench/game/game-export-v{N+1}.schema.json`:

- Change `"const": N` to `"const": N+1` in the `version` property
- Update `$id`, `title`, `description` to reference v{N+1}
- Add new fields to `properties`
- Add new `$defs` if needed

## Step 3: Extend the migration module

Add `vN <-> v{N+1}` support to `src/magebench/game/game_export_migrations.py`:

```python
"""Migration: vN -> v{N+1} (description of what changes)."""

SOURCE_VERSION = N
TARGET_VERSION = N + 1

def up(data: dict) -> dict:
    """Migrate from vN to v{N+1}."""
    assert data["version"] == N, f"Expected vN, got v{data['version']}"
    # Add new fields here
    data["version"] = N + 1
    return data

def down(data: dict) -> dict:
    """Migrate from v{N+1} to vN."""
    assert data["version"] == N + 1, f"Expected v{N+1}, got v{data['version']}"
    # Remove new fields here
    data["version"] = N
    return data
```

The migration must satisfy: `down(up(game)) == game` for all exported games.

## Step 4: Update `src/magebench/game/export_game.py`

1. Change `"version": N` to `"version": N+1` in `build_export()`
2. Add computation for new fields in `build_export()`
3. Update `_validate_export()`: change `version == N` to `version == N+1`
4. Update the comment referencing the schema filename

## Step 5: Update schema references

These files reference the schema filename and need updating:

- `Makefile` (`regen-schema-types` and `verify-schema-types` targets) → `game-export-v{N+1}.schema.json`
- `.claude/hooks/enforce-agents-rules.py` — no change needed (uses glob pattern)
- `doc/export-schema.md` — update prose reference if it mentions a specific version

## Step 6: Regenerate TypeScript types

```bash
make regen-schema-types
```

Verify the generated types look correct in `website/src/types/game-export.d.ts`.

## Step 7: Add tests

In `tests/test_export_schema.py`, add:

- `test_v{N+1}_schema_is_valid` — validates the new schema structure
- `test_v{N+1}_schema_accepts_v{N+1}` — minimal valid export passes
- `test_v{N+1}_schema_rejects_vN` — old version is rejected

In `tests/test_game_export_migrations.py`, add migration tests:

- `test_vN_to_v{N+1}_up_adds_fields` — verify up() adds the right fields
- `test_v{N+1}_to_vN_down_removes_fields` — verify down() strips them
- `test_round_trip_preserves_vN_structure` — `down(up(game)) == game`

When raw-export helpers are intentionally schema-light (for example `load_raw_game_export()` tests), make the migration functions tolerant of partial payloads. Only assert on `version` and transform optional sections when they are present.

## Step 8: Run checks

```bash
make check
```

All lint, typecheck, and tests must pass before proceeding.

## Step 9: Create the PR (usually code only — data migration optional)

Default to a code-only PR. Game migrations touch hundreds of JSON files and GitHub cannot render large diffs. If the issue explicitly asks for a bounded in-repo migration (for example one season only), migrate only that slice and file a follow-up issue for the remaining historical exports.

1. Create the PR with the code changes (schema, migration module, export_game.py, tests, docs, TypeScript types).
2. If you did not migrate all committed exports, note the remaining migration scope in the PR description and file a follow-up issue.

## Step 10: Follow-up migration

This repo does not currently have a generic export migration runner. For bounded migrations, use `src/magebench/game/game_exports.py` helpers (`load_raw_game_export()` / `write_raw_game_export()`) or add a one-off script in the PR. Verify the migrated files with `make check`.
