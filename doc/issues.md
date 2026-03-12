# Issues

Issues are stored as individual JSON files in the `issues/` directory. The filename serves as the issue ID (e.g., `commander-zone-gy-exile-layout.json`).

Resolved issues should be deleted, not marked as resolved/closed.

## Format

```json
{
  "title": "Short summary of the issue",
  "description": "Full description with context...",
  "status": "open",
  "priority": 3,
  "type": "task",
  "labels": ["spectator"],
  "created_at": "2026-02-09T14:30:00.000000-08:00",
  "updated_at": "2026-02-09T14:30:00.000000-08:00"
}
```

Use real timestamps (the actual time you're creating the issue), not `00:00:00` placeholders.

### Fields

| Field | Type | Description |
| ------- | ------ | ------------- |
| `title` | string | Short summary |
| `description` | string | Full description with context |
| `status` | string | Always "open" (delete closed issues) |
| `priority` | int | 1 (highest) to 4 (lowest) |
| `type` | string | Usually "task" |
| `labels` | string[] | Tags like "spectator", "bridge", "puppeteer" |
| `created_at` | string | ISO 8601 timestamp |
| `updated_at` | string | ISO 8601 timestamp |
| `not_autoclaimable` | bool? | If true, `autoclaim-issue.py` skips this issue (has preconditions). **Do NOT set this unless Gregor explicitly says to.** Most issues should be autoclaimable. |

## Querying

### List all issues

```bash
ls issues/
```

### View an issue

```bash
jq . issues/commander-zone-gy-exile-layout.json
```

### List all issue titles with priority

```bash
uv run python scripts/list-issues.py
```

### Find issues by label

```bash
uv run python scripts/query-issues.py --label spectator
```

### Find high priority issues (priority 1-2)

```bash
uv run python scripts/query-issues.py --max-priority 2
```

### Search titles and descriptions

```bash
uv run python scripts/query-issues.py --search "streaming"
```
