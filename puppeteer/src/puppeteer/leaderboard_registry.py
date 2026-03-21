"""Registry and display-name helpers for leaderboard generation."""

from __future__ import annotations

import json
from pathlib import Path

_PROVIDER_DISPLAY: dict[str, str] = {
    "anthropic": "Anthropic",
    "google": "Google",
    "openai": "OpenAI",
    "mistralai": "Mistral AI",
    "deepseek": "DeepSeek",
    "meta-llama": "Meta",
    "x-ai": "xAI",
}


def capitalize_provider(slug: str) -> str:
    """Convert provider slug to display name."""
    return _PROVIDER_DISPLAY.get(slug, slug.title())


def derive_display_name(model_id: str) -> str:
    """Derive a display name from a model ID not in the registry."""
    slug = model_id.split("/", 1)[-1]
    return slug.replace("-", " ").title()


def load_model_registry(models_json: Path) -> dict[str, str]:
    """Load model ID -> display name mapping from models.json."""
    if not models_json.exists():
        return {}
    data = json.loads(models_json.read_text())
    assert isinstance(data, dict), f"{models_json}: expected JSON object"
    models = data["models"]
    assert isinstance(models, list), f"{models_json}: models must be a list"
    registry: dict[str, str] = {}
    for index, model in enumerate(models):
        assert isinstance(model, dict), f"{models_json}: models[{index}] must be an object"
        model_id = model.get("id")
        model_name = model.get("name")
        assert isinstance(model_id, str) and model_id, f"{models_json}: models[{index}] missing id"
        assert isinstance(model_name, str) and model_name, f"{models_json}: models[{index}] missing name"
        registry[model_id] = model_name
    return registry


def _load_inactive_statuses(presets_json: Path) -> dict[str, str] | None:
    """Load inactive statuses for non-active presets from presets.json."""
    if not presets_json.exists():
        return None
    data = json.loads(presets_json.read_text())
    presets = data["presets"]
    statuses: dict[str, str] = {}
    for preset in presets.values():
        status = preset.get("status", "retired")
        if status == "active":
            continue
        model_id = preset["model"]
        effort = preset.get("reasoning_effort")
        key = f"{model_id}::{effort}" if effort else model_id
        statuses[key] = status
    return statuses
