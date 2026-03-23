"""CLI wrapper for blunder analysis."""

import sys

from magebench.analysis.blunder.blunder_analysis import main as run_blunder_analysis


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <game.json.gz | game_id>")
        raise SystemExit(1)
    run_blunder_analysis(sys.argv[1])


if __name__ == "__main__":
    main()
