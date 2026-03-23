"""LLM interaction helpers for blunder analysis."""

import json
import re
import time
from typing import Any

from openai import OpenAI, OpenAIError

from puppeteer.llm_cost import get_model_price

LLM_REQUIRED_FIELDS = {"severity", "description", "action_taken", "better_line"}


def compute_cost(
    prices: dict[str, tuple[float, float]],
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    price = get_model_price(model, prices)
    assert price is not None, f"No pricing found for model {model}"
    input_price, output_price = price
    return (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000


def call_llm(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    retries: int = 3,
) -> tuple[str, int, int, int]:
    """Call LLM with retry on server errors.

    Returns (text, prompt_tokens, completion_tokens, cached_tokens).
    """
    for attempt in range(retries + 1):
        # cache_control is an OpenRouter/Anthropic vendor extension
        # not in OpenAI's type stubs — typed as Any to bypass
        system_msg: Any = {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    system_msg,
                    {"role": "user", "content": user},
                ],
                max_tokens=16384,
            )
        except OpenAIError as e:
            err_str = str(e)
            retryable = "500" in err_str or "502" in err_str or "503" in err_str or "401" in err_str
            if attempt < retries and retryable:
                print(f"    Retrying after error (attempt {attempt + 1})...")
                time.sleep(2 ** (attempt + 1))
            else:
                raise
        text = response.choices[0].message.content
        assert text is not None, "LLM returned no content"
        usage = response.usage
        assert usage is not None, "API response missing usage data"
        cached = 0
        ptd = usage.prompt_tokens_details
        if ptd is not None and ptd.cached_tokens is not None:
            cached = ptd.cached_tokens
        return text, usage.prompt_tokens, usage.completion_tokens, cached
    raise AssertionError(f"unreachable: loop over {retries + 1} attempts completed without return or raise")


def parse_annotation(text: str) -> dict | None:
    """Parse a JSON annotation (object or null) from LLM response.

    Strips markdown fences if present. Returns None for null/empty responses,
    or a dict for a blunder annotation.
    """
    text = text.strip()
    # Strip markdown code fences (may appear at start or after analysis text)
    fence_match = re.search(r"```(?:json)?\s*\n", text)
    if fence_match:
        after_fence = text[fence_match.end() :]
        close = after_fence.find("```")
        text = after_fence[:close].strip() if close != -1 else after_fence.strip()

    # Check for null-like responses
    text_lower = text.lower()
    if text_lower in ("null", "[]", "none"):
        return None

    # Look for a JSON object — must start with `{"` or `{word:` (not mana like {T}, {1})
    json_match = re.search(r'\{\s*"|\{\w+\s*:', text)
    if json_match is None:
        # No JSON object — if text is analysis concluding "reasonable", treat as null
        if (
            "null" in text_lower
            or "no blunder" in text_lower
            or "reasonable" in text_lower
            or "not a blunder" in text_lower
        ):
            return None
        raise AssertionError(f"No JSON found and can't interpret as null:\n{text[:500]}")

    start = json_match.start()
    end = text.rfind("}")
    assert end > start, f"Unmatched braces in response:\n{text[:500]}"
    json_str = text[start : end + 1]

    try:
        result = json.loads(json_str)
    except json.JSONDecodeError:
        # Fix common LLM JSON errors: unquoted keys
        fixed = re.sub(r"(?<=\{|,)\s*(\w+)\s*:", r' "\1":', json_str)
        result = json.loads(fixed)

    if result is None:
        return None
    if isinstance(result, list):
        return result[0] if result else None
    assert isinstance(result, dict), f"Expected JSON object or null, got {type(result).__name__}"
    return result
