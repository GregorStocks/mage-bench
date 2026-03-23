"""CLI wrapper for structured decision extraction."""

import sys

from magebench.analysis.blunder.extract_decisions import main as run_extract_decisions


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <game.json.gz | game_id>")
        raise SystemExit(1)
    run_extract_decisions(sys.argv[1])


if __name__ == "__main__":
    main()
