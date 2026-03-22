# Python `magebench` Migration Plan

This document is the execution plan for moving the repo's Python code into
`src/magebench/` while preserving the package DAG defined in
[`src/magebench/README.md`](../src/magebench/README.md).

## Current State

Step 0 is already done in this branch:

- `src/magebench/` exists with the target top-level packages
- a weird test ratchets the allowed package DAG
- repo lint/typecheck now include `src/`

The remaining work is to move real code into those packages and delete the old
Python roots.

## Constraints

- Keep the `magebench` top-level package graph acyclic.
- Keep `common` boring: generic helpers only, no game-domain types.
- Treat `game` as the canonical home for the v8 export/game data model.
- Keep `cli` thin. Business logic should live in library packages, not command
  wrappers.
- Move code package-by-package, not as one giant rename.
- Avoid long-lived import alias shims. During the migration it is fine for old
  and new roots to coexist, but each moved module should end with exactly one
  canonical implementation.
- Keep the repo green after each step.

## Dependency Tracking

The issue schema does not have a dedicated dependency field. For this plan:

- Step 1 is claimable immediately.
- Later step issues use `blocked-python-migration-stepN.json5`.
- Their `blocked` strings reference prerequisite step numbers from this file.
- When a prerequisite step lands, the next issue should be renamed from
  `blocked-python-migration-stepN.json5` to
  `p{priority}-python-migration-stepN.json5` and have its `blocked` field
  removed.

## Steps

### Step 1: Wire `src/magebench` Into Packaging and Import Resolution

Dependencies: none

Scope:

- make `magebench` importable via repo tooling (`uv run`, tests, scripts)
- update packaging metadata so `src/magebench` is a first-class package root
- add a smoke test that imports `magebench`
- do not move large behavior in this step

### Step 2: Drop Pre-v8 Export History and Migration Machinery

Dependencies: Step 1

Scope:

- delete `schemas/migrations/`
- delete `scripts/migrate_exports.py`
- delete `game-export-v2` through `game-export-v7` schema files
- remove tests and docs that only exist for old export versions
- keep the v8 schema and runtime types

### Step 3: Move Canonical Game Types and Schema Into `magebench.game`

Dependencies: Step 2

Scope:

- move `schemas/game_export_types.py` into `src/magebench/game/`
- move `schemas/game-export-v8.schema.json` under `src/magebench/game/`
- rewrite imports from `schemas.*` to `magebench.game.*`
- update schema consumers such as website type generation and export validation

### Step 4: Move Leaf Shared Helpers Into `magebench.common`

Dependencies: Step 1

Scope:

- move low-level helpers with no game-domain knowledge into `common`
- expected early candidates: JSON/JSON5 helpers, HTTP helpers, issue-file
  helpers, and other similar leaf utilities
- avoid turning `common` into a junk drawer

### Step 5: Move the Export/Build Pipeline Into `magebench.game`

Dependencies: Steps 3 and 4

Scope:

- move the export builder and related helpers into `src/magebench/game/`
- this includes the code that reads logs, builds decisions, computes export
  metadata, and writes website-ready game files
- after this step, export logic should no longer live in `scripts/`

### Step 6: Move Leaderboard Code Into `magebench.leaderboard`

Dependencies: Step 5

Scope:

- move the leaderboard library modules out of `puppeteer/src/puppeteer/`
- move reusable logic from `scripts/generate_leaderboard.py`
- leave only thin CLI entrypoints behind

### Step 7: Move Blunder Analysis Core Into `magebench.analysis.blunder`

Dependencies: Step 5

Scope:

- move blunder analysis, annotation, and decision-extraction code into
  `src/magebench/analysis/blunder/`
- keep the shared decision/game model in `magebench.game`, not in `analysis`

### Step 8: Move Toolbox Utilities Into `magebench.analysis.toolbox`

Dependencies: Step 7

Scope:

- move the one-off inspection and research utilities from
  `scripts/analysis/toolbox/`
- keep them as consumers of the stabilized `game` and `analysis.blunder`
  packages

### Step 9: Move Pilot Runtime Code Into `magebench.pilot`

Dependencies: Steps 3 and 4

Scope:

- move pilot-facing runtime modules out of `puppeteer/src/puppeteer/`
- this includes the pilot loop, decision rendering, bridge/runtime helpers,
  replay helpers, and closely related support modules
- keep `pilot` depending downward on `game` and `common`, not sideways on
  `cli`

### Step 10: Move Orchestration Code Into `magebench.orchestration`

Dependencies: Steps 6, 7, and 9

Scope:

- move orchestration, process lifecycle, batch coordination, post-game wiring,
  and related runtime management code
- this is intentionally late because it ties together pilot, analysis,
  leaderboard, and game/export code

### Step 11: Move Python Tests to Top-Level `tests/`

Dependencies: Steps 6, 8, 9, and 10

Scope:

- move Python tests out of `puppeteer/tests/` into a top-level `tests/`
  package
- update helpers, repo-convention tests, and paths to the new package layout
- update AGENTS/docs/test instructions after the test move is real

### Step 12: Finalize `magebench.cli` and Delete Legacy Python Roots

Dependencies: Step 11

Scope:

- replace legacy command entrypoints with thin `magebench.cli` wrappers
- update Makefile commands, docs, and local workflows to the final paths
- delete `scripts/`, `schemas/`, and `puppeteer/src/puppeteer/`
- remove stale packaging/workspace references to the old roots
