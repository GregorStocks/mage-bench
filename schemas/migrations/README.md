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

- v2: Active schema (`schemas/game-export-v2.schema.json`)
- No migrations yet — this directory is infrastructure for the next version bump.
