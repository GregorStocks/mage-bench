"""Migration: v4 -> v5 (normalize chosenArgs arrays to CSV strings).

Old games stored mana_plan, attackers, and blockers as JSON arrays in
chosenArgs.  Epoch 36+ switched to comma-separated strings.  This
migration normalises old games so the renderer can assume strings.
"""

SOURCE_VERSION = 4
TARGET_VERSION = 5

# Fields in chosenArgs that were changed from arrays to CSV strings.
_CSV_FIELDS = ("mana_plan", "attackers", "blockers")


def _list_to_csv(value: object) -> str:
    """Convert a list (or already-string) field to a CSV string."""
    if isinstance(value, str):
        return value
    assert isinstance(value, list), f"Expected list or str, got {type(value).__name__}"
    return ",".join(str(item) for item in value)


def _csv_to_list(value: object) -> list:
    """Convert a CSV string back to a list of strings (for down migration)."""
    if isinstance(value, list):
        return value
    assert isinstance(value, str), f"Expected str or list, got {type(value).__name__}"
    if not value:
        return []
    return value.split(",")


def up(data: dict) -> dict:
    """Migrate from v4 to v5: normalise chosenArgs arrays to CSV strings."""
    assert data["version"] == 4, f"Expected v4, got v{data['version']}"

    for decision in data.get("decisions", []):
        args = decision.get("chosenArgs")
        if not args:
            continue
        for field in _CSV_FIELDS:
            if field in args:
                args[field] = _list_to_csv(args[field])

    data["version"] = 5
    return data


def down(data: dict) -> dict:
    """Migrate from v5 to v4: convert CSV strings back to arrays."""
    assert data["version"] == 5, f"Expected v5, got v{data['version']}"

    for decision in data.get("decisions", []):
        args = decision.get("chosenArgs")
        if not args:
            continue
        for field in _CSV_FIELDS:
            if field in args:
                args[field] = _csv_to_list(args[field])

    data["version"] = 4
    return data
