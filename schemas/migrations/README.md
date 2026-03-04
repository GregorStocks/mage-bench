# Export Schema Migrations

Each version bump (e.g., v3 to v4) gets a migration module in this directory.

## Pattern

Each migration module exports two functions and two version constants:

```python
SOURCE_VERSION = 3
TARGET_VERSION = 4

def up(data: dict) -> dict:
    """Migrate from version N to N+1. Mutates and returns data."""
    ...

def down(data: dict) -> dict:
    """Migrate from version N+1 back to N. Mutates and returns data."""
    ...
```

The `__init__.py` registry lists all modules in the `MIGRATIONS` list (ordered by
SOURCE_VERSION ascending). The shared runner at `scripts/migrate_exports.py` uses
this registry to find and chain migrations.

## Runner

```bash
# Migrate all games to a target version
uv run python scripts/migrate_exports.py --to 4 [--dry-run] [--force]
```

The runner automatically chains migrations (e.g., v2 → v3 → v4) and handles
.json/.json.gz file I/O.

## Invariants

- **Roundtrip**: `down(up(game)) == game` for every exported game. This is
  enforced by CI tests in `test_migrate_exports.py`.
- **At most two versions coexist** at any time. Once all games are migrated,
  the old version's migration module stays for historical reference.
- **Incremental adoption**: Land the new schema first, migrate games across
  multiple PRs, verify with `make check`.

## Cutting a new version

Use the `/new-export-version` skill for step-by-step guidance.

## Current state

- v6: Active export version (`schemas/game-export-v6.schema.json`)
- v2 → v3: `schemas/migrations/v2_to_v3.py` (adds cardData, token images)
- v3 → v4: `schemas/migrations/v3_to_v4.py` (adds season, tournament)
- v4 → v5: `schemas/migrations/v4_to_v5.py` (normalize chosenArgs arrays to CSV strings)
- v5 → v6: `schemas/migrations/v5_to_v6.py` (removes llmTrace)
