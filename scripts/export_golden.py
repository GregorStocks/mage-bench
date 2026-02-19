"""Export golden test fixtures as .json.gz game files for the website viewer.

Reads Java golden fixtures (MCP game_state + pass_priority_result) and
produces .json.gz files in the same format as real game exports, so the
existing game viewer can display them.

Usage:
    uv run python scripts/export_golden.py
"""

import gzip
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = (
    REPO_ROOT / "Mage.Tests" / "src" / "test" / "resources" / "golden" / "mcp"
)
OUTPUT_DIR = REPO_ROOT / "website" / "public" / "golden"


def _fixture_to_game_export(name: str, fixture: dict) -> dict:
    """Convert a golden fixture to the game export format.

    The existing game viewer expects:
      - snapshots: array of board states
      - players: array of player metadata
      - actions: array of game actions (optional)
      - cardImages: map of card name -> scryfall URL (optional)
    """
    gs = fixture["game_state"]

    # Build a snapshot from the MCP game_state
    snapshot_players = []
    export_players = []
    for p in gs.get("players", []):
        sp: dict = {
            "name": p["name"],
            "life": p.get("life"),
            "library_count": p.get("library_size"),
            "hand_count": p.get("hand_size", len(p.get("hand", []))),
            "battlefield": p.get("battlefield", []),
            "hand": p.get("hand", []),
            "graveyard": p.get("graveyard", []),
            "exile": p.get("exile", []),
            "commanders": p.get("commanders", []),
        }
        snapshot_players.append(sp)

        export_players.append(
            {
                "name": p["name"],
                "type": "golden-test",
            }
        )

    snapshot = {
        "turn": gs.get("turn"),
        "phase": gs.get("phase"),
        "step": gs.get("step"),
        "active_player": gs.get("active_player"),
        "priority_player": gs.get("priority_player"),
        "stack": gs.get("stack", []),
        "players": snapshot_players,
    }

    # Build the pass_priority result as an "action" so it shows in the timeline
    ppr = fixture.get("pass_priority_result", {})
    actions = []
    if ppr:
        msg = ppr.get("message", "")
        if ppr.get("action_type"):
            msg = f"[{ppr['action_type']}] {msg}"
        actions.append(
            {
                "ts": "00:00:00",
                "seq": 0,
                "message": msg,
            }
        )

    return {
        "id": f"golden_{name}",
        "timestamp": "golden_test",
        "gameType": "Golden Test",
        "deckType": "N/A",
        "totalTurns": gs.get("turn", 1),
        "players": export_players,
        "snapshots": [snapshot],
        "actions": actions,
        "cardImages": {},
        # Include the raw MCP data for reference
        "goldenFixture": {
            "mcp_game_state": gs,
            "mcp_pass_priority": ppr,
        },
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    exported = 0
    for fixture_file in sorted(FIXTURES_DIR.glob("*.json")):
        # Skip prompt_context.json — it has no game_state
        if fixture_file.name == "prompt_context.json":
            continue

        fixture = json.loads(fixture_file.read_text())
        if "game_state" not in fixture:
            continue

        name = fixture_file.stem
        game_export = _fixture_to_game_export(name, fixture)

        out_path = OUTPUT_DIR / f"golden_{name}.json.gz"
        with gzip.open(out_path, "wt", encoding="utf-8") as f:
            json.dump(game_export, f, indent=2)

        exported += 1
        print(f"  {fixture_file.name} -> {out_path.relative_to(REPO_ROOT)}")

    print(
        f"Exported {exported} golden fixtures to {OUTPUT_DIR.relative_to(REPO_ROOT)}/"
    )


if __name__ == "__main__":
    main()
