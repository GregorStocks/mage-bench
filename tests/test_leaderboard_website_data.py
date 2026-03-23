"""Tests for leaderboard website-data orchestration."""

import json

import magebench.leaderboard.website_data as website_data


def test_copy_season_data_copies_authoritative_file(tmp_path):
    data_dir = tmp_path / "website" / "src" / "data"
    season_json = tmp_path / "data" / "season.json"
    season_json.parent.mkdir(parents=True)
    season_json.write_text(json.dumps({"current_season": 7, "phase": "regular-season"}))

    output_path = website_data.copy_season_data(data_dir=data_dir, season_json=season_json)

    assert output_path == data_dir / "season.json"
    assert json.loads(output_path.read_text()) == {
        "current_season": 7,
        "phase": "regular-season",
    }


def test_generate_all_website_data_copies_season_before_generating_outputs(tmp_path, monkeypatch):
    games_dir = tmp_path / "website" / "public" / "games"
    data_dir = tmp_path / "website" / "src" / "data"
    models_json = tmp_path / "puppeteer" / "models.json"
    season_json = tmp_path / "data" / "season.json"
    games_dir.mkdir(parents=True)
    models_json.parent.mkdir(parents=True)
    models_json.write_text(json.dumps({"models": []}))
    season_json.parent.mkdir(parents=True)
    season_json.write_text(json.dumps({"current_season": 3}))

    calls: list[tuple[str, object]] = []

    def fake_generate_leaderboard_file(
        games_dir_arg,
        data_dir_arg,
        models_json_arg,
        *,
        current_season=None,
    ):
        assert json.loads((data_dir_arg / "season.json").read_text()) == {"current_season": 3}
        calls.append(("leaderboard", current_season))
        assert games_dir_arg == games_dir
        assert data_dir_arg == data_dir
        assert models_json_arg == models_json
        return data_dir_arg / "benchmark-results.json"

    def fake_generate_model_stats(games_dir_arg, data_dir_arg, models_json_arg):
        calls.append(("model_stats", games_dir_arg, data_dir_arg, models_json_arg))
        return data_dir_arg / "model-stats.json"

    def fake_generate_internals_data(games_dir_arg, data_dir_arg, models_json_arg):
        calls.append(("internals", games_dir_arg, data_dir_arg, models_json_arg))
        return data_dir_arg / "internals-data.json"

    def fake_generate_blunder_stats(data_dir_arg):
        calls.append(("blunder", data_dir_arg))
        return data_dir_arg / "blunder-internals.json"

    monkeypatch.setattr(website_data, "generate_leaderboard_file", fake_generate_leaderboard_file)
    monkeypatch.setattr(website_data, "generate_model_stats", fake_generate_model_stats)
    monkeypatch.setattr(website_data, "generate_internals_data", fake_generate_internals_data)
    monkeypatch.setattr(website_data, "generate_blunder_stats", fake_generate_blunder_stats)

    website_data.generate_all_website_data(
        games_dir=games_dir,
        data_dir=data_dir,
        models_json=models_json,
        season_json=season_json,
    )

    assert calls == [
        ("leaderboard", 3),
        ("model_stats", games_dir, data_dir, models_json),
        ("internals", games_dir, data_dir, models_json),
        ("blunder", data_dir),
    ]
