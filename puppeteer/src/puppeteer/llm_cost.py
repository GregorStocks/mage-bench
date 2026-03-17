"""Shared LLM cost tracking utilities.

Fetches live model pricing from OpenRouter at startup and provides
helpers for cost estimation and file-based cost reporting.
"""

import json
import urllib.request
from pathlib import Path

from puppeteer.log import get_logger

logger = get_logger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
FETCH_TIMEOUT_SECS = 10
DEFAULT_LLM_PROVIDER = "openrouter"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_PROVIDER_BASE_URLS = {
    "openrouter": DEFAULT_BASE_URL,
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}
_PROVIDER_API_KEY_ENVS = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}
SUPPORTED_LLM_PROVIDERS = tuple(_PROVIDER_BASE_URLS)


def _resolve_llm_provider(provider: str | None) -> str:
    """Resolve a provider slug to a supported value."""
    if provider is None:
        return DEFAULT_LLM_PROVIDER
    if provider in _PROVIDER_BASE_URLS:
        return provider
    supported = ", ".join(SUPPORTED_LLM_PROVIDERS)
    raise ValueError(f"Unknown LLM provider: {provider!r}. Supported providers: {supported}.")


def llm_base_url(provider: str | None) -> str:
    """Map a provider slug to its OpenAI-compatible base URL."""
    return _PROVIDER_BASE_URLS[_resolve_llm_provider(provider)]


def required_api_key_env(provider: str | None) -> str:
    """Infer the expected API key env var from the configured provider."""
    return _PROVIDER_API_KEY_ENVS[_resolve_llm_provider(provider)]


def fetch_openrouter_prices() -> dict[str, tuple[float, float]]:
    """Fetch model pricing from OpenRouter.

    Returns {model_id: (input_per_1M_tokens, output_per_1M_tokens)}.
    Returns empty dict on any failure.
    """
    try:
        req = urllib.request.Request(OPENROUTER_MODELS_URL)
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECS) as resp:
            data = json.loads(resp.read())
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("[llm_cost] Failed to fetch OpenRouter prices: %s", e)
        return {}

    prices: dict[str, tuple[float, float]] = {}
    models = data.get("data")
    if models is None:
        logger.warning("[llm_cost] OpenRouter response missing 'data' field")
        return {}
    for model in models:
        model_id = model.get("id", "")
        pricing = model.get("pricing")
        if not model_id or not pricing:
            continue
        try:
            prompt_per_token = float(pricing.get("prompt") or "0")
            completion_per_token = float(pricing.get("completion") or "0")
            prices[model_id] = (
                prompt_per_token * 1_000_000,
                completion_per_token * 1_000_000,
            )
        except (ValueError, TypeError):
            continue
    return prices


def load_prices() -> dict[str, tuple[float, float]]:
    """Fetch OpenRouter prices at startup. Returns empty dict on failure."""
    prices = fetch_openrouter_prices()
    if prices:
        logger.info("[llm_cost] Loaded pricing for %d models from OpenRouter", len(prices))
    else:
        logger.warning("[llm_cost] Could not fetch OpenRouter prices; cost tracking disabled")
    return prices


def get_model_price(model: str, prices: dict[str, tuple[float, float]]) -> tuple[float, float] | None:
    """Get (input, output) price per 1M tokens, or None if unknown."""
    if model in prices:
        return prices[model]
    best_match = ""
    for candidate in prices:
        if model.startswith(candidate) and len(candidate) > len(best_match):
            best_match = candidate
    if best_match:
        return prices[best_match]
    return None


def write_cost_file(game_dir: Path, username: str, cost: float) -> None:
    """Write cumulative cost to a JSON file for the observer client to read."""
    cost_file = game_dir / f"{username}_cost.json"
    tmp_file = cost_file.with_suffix(".tmp")
    try:
        tmp_file.write_text(json.dumps({"cost_usd": cost}))
        tmp_file.rename(cost_file)
    except OSError as e:
        logger.error("[llm_cost] Failed to write cost file: %s", e)
