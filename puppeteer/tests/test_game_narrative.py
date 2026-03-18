"""Tests for scripts/analysis/toolbox/game_narrative.py."""

import scripts.analysis.toolbox.game_narrative as game_narrative
from schemas.game_export_types import Permanent


def test_main_formats_dataclass_battlefield_names(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        game_narrative,
        "load_game",
        lambda _path: {
            "snapshots": [
                {
                    "turn": 1,
                    "players": [
                        {
                            "name": "Alice",
                            "life": 20,
                            "hand": [],
                            "battlefield": [Permanent(name="Memnite")],
                        }
                    ],
                }
            ],
            "actions": [],
        },
    )

    game_narrative.main("game_test_001.json")

    out = capsys.readouterr().out
    assert "bf=[Memnite]" in out
    assert "Permanent(" not in out
