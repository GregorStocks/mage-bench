"""Verify all scripts in scripts/ are Python, not Bash or other languages."""

import sys
from pathlib import Path

# File extensions that are data, not scripts — skip these.
DATA_EXTENSIONS = {".json", ".gitkeep", ".html"}


def lint_scripts(project_root: Path) -> list[str]:
    scripts_dir = project_root / "scripts"
    errors = []

    for path in sorted(scripts_dir.rglob("*")):
        if path.is_dir():
            continue
        if "__pycache__" in path.parts:
            continue
        if path.suffix in DATA_EXTENSIONS or path.name == ".gitkeep":
            continue
        if path.suffix != ".py":
            rel = path.relative_to(project_root)
            errors.append(f"{rel}: not a Python script (suffix: {path.suffix or 'none'})")

    return errors


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent.parent
    errors = lint_scripts(project_root)

    if errors:
        print("Script language errors:", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        sys.exit(1)

    print("Scripts: OK")


if __name__ == "__main__":
    main()
