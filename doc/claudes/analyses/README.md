# Game Analyses

Two subdirectories, one per analysis type:

- `fast/` — from `/fast-analysis` (gz-only triage)
- `deep/` — from `/analyze-game` (full log deep-dive)

One file per game, named `game_YYYYMMDD_HHMMSS.md`. Each skill checks its own directory to find unanalyzed games. A game can have both a fast and deep analysis.

## Template

```markdown
# game_YYYYMMDD_HHMMSS

**Players**: Player1 (model) vs Player2 (model)
**Format**: Standard|Commander|Modern
**Result**: Winner won on turn N
**Config**: config-name

## Findings

- Finding or issue filed
- ...

## Notes

Anything worth remembering for future Claudes.
```
