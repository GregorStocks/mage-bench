# `magebench`

This directory is the future home for the repo's Python code.

The goal is one import namespace, `magebench.*`, with a package-level
dependency graph that stays acyclic.

## Structure

- `common`: generic shared helpers with no game-domain knowledge
- `game`: canonical game/export data model and related I/O
- `analysis`: offline and post-game analysis code
- `leaderboard`: leaderboard and aggregate reporting code
- `pilot`: in-game pilot/runtime logic
- `orchestration`: code that wires together game lifecycle pieces
- `cli`: thin command entrypoints only

`analysis/` is split into:

- `analysis/blunder`: blunder annotation and evaluation
- `analysis/toolbox`: one-off analysis utilities and inspection tools

## Dependency DAG

The allowed top-level package dependencies are:

- `common` -> nothing internal
- `game` -> `common`
- `analysis` -> `common`, `game`
- `leaderboard` -> `common`, `game`
- `pilot` -> `common`, `game`
- `orchestration` -> `common`, `game`, `analysis`, `leaderboard`, `pilot`
- `cli` -> all of the above

Nothing should import `cli`, and `magebench/__init__.py` should stay thin.

The weird test at
`puppeteer/tests/weird/test_magebench_package_dag.py` ratchets this DAG.
