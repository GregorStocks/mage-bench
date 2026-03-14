"""Shared LLM cost tracking utilities.

Fetches live model pricing from OpenRouter at startup and provides
helpers for cost estimation and file-based cost reporting.
"""

import json
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from puppeteer.log import get_logger

logger = get_logger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
FETCH_TIMEOUT_SECS = 10
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_PROVIDER_API_KEY_ENVS = {
    "openrouter.ai": "OPENROUTER_API_KEY",
    "api.openai.com": "OPENAI_API_KEY",
    "api.anthropic.com": "ANTHROPIC_API_KEY",
    "generativelanguage.googleapis.com": "GEMINI_API_KEY",
}


def redact_base_url_for_log(base_url: str) -> str:
    """Strip credentials and query or fragment data before logging a base URL."""
    url = base_url or DEFAULT_BASE_URL
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return url.rsplit("@", 1)[-1].split("?", 1)[0].split("#", 1)[0]

    netloc = parsed.netloc.rsplit("@", 1)[-1]
    hostname = parsed.hostname
    if hostname is not None:
        host = hostname if ":" not in hostname else f"[{hostname}]"
        netloc = host
        try:
            port = parsed.port
        except ValueError:
            port = None
        if port is not None:
            netloc = f"{netloc}:{port}"

    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def required_api_key_env(base_url: str) -> str:
    """Infer the expected API key env var from an exact provider hostname."""
    url = base_url or DEFAULT_BASE_URL
    parsed = urlsplit(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    if parsed.scheme not in ("http", "https") or not host:
        raise ValueError(f"Invalid LLM base URL: {redact_base_url_for_log(url)}")

    if host in _PROVIDER_API_KEY_ENVS:
        return _PROVIDER_API_KEY_ENVS[host]

    supported_hosts = ", ".join(sorted(_PROVIDER_API_KEY_ENVS))
    raise ValueError(
        "Unsupported LLM base URL host for automatic API key lookup: "
        f"{host}. Supported hosts: {supported_hosts}."
    )


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
    for model in data.get("data", []):
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
