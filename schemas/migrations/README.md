# Export Schema Migrations

Each version bump (e.g., v2 to v3) gets a migration module in this directory.

## Pattern

Each migration module exports two functions:

```python
def up(data: dict) -> dict:
    """Migrate from version N to N+1."""
    ...

def down(data: dict) -> dict:
    """Migrate from version N+1 back to N."""
    ...
```

## Invariants

- **Roundtrip**: `down(up(game)) == game` for every exported game. This is
  enforced by a CI test when a migration exists.
- **At most two versions coexist** at any time. Once all games are migrated,
  delete the old version's migration code and schema.
- **Incremental adoption**: Land the new schema first, migrate games across
  multiple PRs, delete migration code once complete.

## Current state

- v4: Active export version (v2 schema file, accepts versions 2/3/4)
- v3 → v4: `scripts/migrate_v3_to_v4.py` (adds `season`, `tournament`)
- v4 → v3: `scripts/migrate_v4_to_v3.py` (removes `season`, `tournament`)
