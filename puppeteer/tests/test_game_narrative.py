"""Tests for magebench.analysis.toolbox.game_narrative."""

from types import SimpleNamespace

import magebench.analysis.toolbox.game_narrative as game_narrative
from magebench.game.game_export_types import Permanent, Snapshot, SnapshotPlayer


def test_main_formats_dataclass_battlefield_names(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        game_narrative,
        "load_game",
        lambda _path: SimpleNamespace(
            snapshots=[
                Snapshot(
                    seq=0,
                    turn=1,
                    phase=None,
                    step=None,
                    active_player=None,
                    priority_player=None,
                    players=[
                        SnapshotPlayer(
                            name="Alice",
                            life=20,
                            library_size=0,
                            hand=[],
                            battlefield=[Permanent(name="Memnite")],
                            graveyard=[],
                        )
                    ],
                    stack=[],
                )
            ],
            actions=[],
        ),
    )

    game_narrative.main("game_test_001.json")

    out = capsys.readouterr().out
    assert "bf=[Memnite]" in out
    assert "Permanent(" not in out
