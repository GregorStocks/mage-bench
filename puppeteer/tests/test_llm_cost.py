"""Tests for LLM cost tracking utilities."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from puppeteer.llm_cost import (
    fetch_openrouter_prices,
    get_model_price,
    required_api_key_env,
    write_cost_file,
)


def test_required_api_key_env_openrouter():
    assert required_api_key_env("https://openrouter.ai/api/v1") == "OPENROUTER_API_KEY"


def test_required_api_key_env_openai():
    assert required_api_key_env("https://api.openai.com/v1") == "OPENAI_API_KEY"


def test_required_api_key_env_anthropic():
    assert required_api_key_env("https://api.anthropic.com/v1") == "ANTHROPIC_API_KEY"


def test_required_api_key_env_google():
    assert required_api_key_env("https://generativelanguage.googleapis.com/v1") == "GEMINI_API_KEY"


def test_required_api_key_env_default():
    assert required_api_key_env("https://custom-llm-host.example.com/v1") == "OPENROUTER_API_KEY"


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
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: response)

    assert fetch_openrouter_prices() == {}
