"""Tests for LLM cost tracking utilities."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from puppeteer.llm_cost import (
    DEFAULT_LLM_PROVIDER,
    fetch_openrouter_prices,
    get_model_price,
    llm_base_url,
    required_api_key_env,
    write_cost_file,
)


def test_default_provider():
    assert DEFAULT_LLM_PROVIDER == "openrouter"


def test_llm_base_url_openrouter():
    assert llm_base_url("openrouter") == "https://openrouter.ai/api/v1"


def test_llm_base_url_openai():
    assert llm_base_url("openai") == "https://api.openai.com/v1"


def test_required_api_key_env_openrouter():
    assert required_api_key_env("openrouter") == "OPENROUTER_API_KEY"


def test_required_api_key_env_openai():
    assert required_api_key_env("openai") == "OPENAI_API_KEY"


def test_required_api_key_env_anthropic():
    assert required_api_key_env("anthropic") == "ANTHROPIC_API_KEY"


def test_required_api_key_env_google():
    assert required_api_key_env("gemini") == "GEMINI_API_KEY"


def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        required_api_key_env("custom-llm-host")


def test_get_model_price_exact():
    prices = {"google/gemini-2.0-flash-001": (0.10, 0.40)}
    result = get_model_price("google/gemini-2.0-flash-001", prices)
    assert result == (0.10, 0.40)


def test_get_model_price_prefix():
    prices = {"google/gemini-2.0-flash": (0.10, 0.40)}
    result = get_model_price("google/gemini-2.0-flash-001", prices)
    assert result == (0.10, 0.40)


def test_get_model_price_best_prefix():
    """When multiple prefixes match, should pick the longest."""
    prices = {
        "google/gemini": (1.0, 2.0),
        "google/gemini-2.0-flash": (0.10, 0.40),
    }
    result = get_model_price("google/gemini-2.0-flash-001", prices)
    assert result == (0.10, 0.40)


def test_get_model_price_unknown():
    prices = {"google/gemini-2.0-flash": (0.10, 0.40)}
    result = get_model_price("anthropic/claude-sonnet-4", prices)
    assert result is None


def test_write_cost_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        game_dir = Path(tmpdir)
        write_cost_file(game_dir, "alice", 1.23)

        cost_file = game_dir / "alice_cost.json"
        assert cost_file.exists()
        data = json.loads(cost_file.read_text())
        assert data == {"cost_usd": 1.23}


def test_fetch_openrouter_prices_returns_empty_dict_on_invalid_json(monkeypatch):
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = None
    response.read.return_value = b"{not json"
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: response)

    assert fetch_openrouter_prices() == {}
