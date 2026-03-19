# Issues

Issues are stored as individual JSON5 files in the `issues/` directory. The filename serves as the issue ID and must start with `p1-`, `p2-`, `p3-`, `p4-`, or `blocked-` (e.g., `p3-commander-zone-gy-exile-layout.json5`).

Resolved issues should be deleted, not marked as resolved/closed.

## Format

```json5
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
| `blocked` | bool \| string? | If truthy, the filename must start with `blocked-` and `autoclaim-issue.py` skips this issue. When a string, it describes *why* the issue is blocked (e.g. `"Waiting for upstream stubs package to be fixed"`). The solve-issue skill checks higher-priority blocked issues before auto-claiming and uses LLM reasoning to determine if the blocker has been resolved. **Do NOT set this unless Gregor explicitly says to.** Most issues should be claimable. |

## Querying

### List all issues

```bash
ls issues/
```

### View an issue

```bash
sed -n '1,160p' issues/p3-commander-zone-gy-exile-layout.json5
```

### List all issue titles with priority

```bash
uv run python scripts/query-issues.py
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
