"""Tests for prompt-loading helpers."""

from pathlib import Path

import pytest

from magebench.pilot.prompts import load_prompts


def test_load_prompts_without_config_uses_repo_prompts_json(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_prompts = tmp_path / "puppeteer"
    (repo_prompts / "prompts").mkdir(parents=True)
    (repo_prompts / "prompts" / "default.md").write_text("markdown default")
    (repo_prompts / "prompts.json").write_text('{"default":"json default","alt":"json alt"}')
    monkeypatch.chdir(tmp_path)

    prompts = load_prompts(None)

    assert prompts["default"] == "json default"
    assert prompts["alt"] == "json alt"


def test_load_prompts_rejects_non_string_json_values(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_prompts = tmp_path / "puppeteer"
    repo_prompts.mkdir(parents=True)
    (repo_prompts / "prompts.json").write_text('{"default": 17}')
    monkeypatch.chdir(tmp_path)

    with pytest.raises(AssertionError, match="must be a string"):
        load_prompts(None)
