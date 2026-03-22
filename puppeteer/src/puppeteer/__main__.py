"""Entry point for puppeteer module."""

# TODO(shim): expires=issue:python-migration-step12 Delete this wrapper once
# callers invoke magebench orchestration via magebench.cli or the new package
# entrypoint directly.
from magebench.orchestration.orchestrator import main

if __name__ == "__main__":
    raise SystemExit(main())
