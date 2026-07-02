"""Tests for scryfall cache override and offline mode."""

import json
from pathlib import Path

import pytest

from magebench.game import scryfall


@pytest.fixture
def scryfall_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point scryfall at a temp cache file and reset its in-memory cache."""
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "token:Rhino": "https://cards.scryfall.io/small/front/2/1/rhino.jpg",
                "Lightning Bolt": {"name": "Lightning Bolt"},
                "Not A Card": None,
            }
        )
    )
    monkeypatch.setenv("MAGEBENCH_SCRYFALL_CACHE", str(cache_path))
    monkeypatch.setenv("MAGEBENCH_SCRYFALL_OFFLINE", "1")
    monkeypatch.setattr(scryfall, "_cache", None)
    return cache_path


def test_offline_cache_hits(scryfall_cache: Path) -> None:
    assert scryfall.search_token("Rhino Token") == (
        "https://cards.scryfall.io/small/front/2/1/rhino.jpg"
    )
    assert scryfall.named("Lightning Bolt") == {"name": "Lightning Bolt"}
    assert scryfall.named("Not A Card") is None
    found, not_found = scryfall.collection(["Lightning Bolt", "Not A Card"])
    assert found == [{"name": "Lightning Bolt"}]
    assert not_found == [{"name": "Not A Card"}]


def test_offline_token_miss_raises(scryfall_cache: Path) -> None:
    with pytest.raises(AssertionError, match="offline mode.*token:Wurm"):
        scryfall.search_token("Wurm Token")


def test_offline_named_miss_raises(scryfall_cache: Path) -> None:
    with pytest.raises(AssertionError, match="offline mode.*Uncached Card"):
        scryfall.named("Uncached Card")


def test_offline_collection_miss_raises(scryfall_cache: Path) -> None:
    with pytest.raises(AssertionError, match="offline mode.*Uncached Card"):
        scryfall.collection(["Lightning Bolt", "Uncached Card"])


def test_cache_path_env_override(scryfall_cache: Path) -> None:
    assert scryfall._cache_path() == scryfall_cache


def test_conftest_pins_tests_to_fixture() -> None:
    """The test suite as a whole must run against the committed fixture."""
    import os

    fixture = Path(__file__).parent / "golden" / "scryfall-cache.json"
    assert os.environ["MAGEBENCH_SCRYFALL_CACHE"] == str(fixture)
    assert os.environ["MAGEBENCH_SCRYFALL_OFFLINE"] == "1"
