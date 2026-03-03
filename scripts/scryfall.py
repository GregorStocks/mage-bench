"""Scryfall API client.

All Scryfall API access goes through this module to ensure compliance with
their API guidelines (https://scryfall.com/docs/api):
- User-Agent and Accept headers on every request
- 50-100ms delay between requests
- Local disk cache (Scryfall asks for 24h minimum)
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_HEADERS = {
    "User-Agent": "mage-bench/1.0",
    "Accept": "application/json",
}

_CACHE_PATH = Path.home() / ".mage-bench" / "scryfall-cache.json"

# Track last request time for rate limiting
_last_request_time: float = 0.0

# In-memory cache, lazily loaded from disk
_cache: dict[str, dict | None] | None = None


def _rate_limit() -> None:
    """Ensure at least 100ms between Scryfall API requests."""
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if _last_request_time > 0 and elapsed < 0.1:
        time.sleep(0.1 - elapsed)
    _last_request_time = time.monotonic()


def _load_cache() -> dict[str, dict | None]:
    """Load cache from disk, or return empty dict."""
    global _cache
    if _cache is None:
        if _CACHE_PATH.exists():
            _cache = json.loads(_CACHE_PATH.read_text())
        else:
            _cache = {}
    return _cache


def _save_cache() -> None:
    """Write cache to disk."""
    if _cache is not None:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(_cache))


def _fetch_collection(names: list[str]) -> tuple[list[dict], list[dict]]:
    """Raw Scryfall /cards/collection request (no caching)."""
    assert len(names) <= 75, f"Scryfall collection batch max is 75, got {len(names)}"
    _rate_limit()
    body = json.dumps({"identifiers": [{"name": n} for n in names]}).encode()
    req = urllib.request.Request(
        "https://api.scryfall.com/cards/collection",
        data=body,
        headers={**_HEADERS, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return data.get("data", []), data.get("not_found", [])


def collection(names: list[str]) -> tuple[list[dict], list[dict]]:
    """Query Scryfall /cards/collection with disk caching.

    Returns (found_cards, not_found_identifiers).
    Cached cards are returned from disk; only uncached names hit the API.
    """
    cache = _load_cache()

    # Split into cached hits, cached misses (not_found), and uncached
    found: list[dict] = []
    not_found: list[dict] = []
    uncached: list[str] = []
    for name in names:
        if name in cache:
            if cache[name] is not None:
                found.append(cache[name])  # type: ignore[arg-type]
            else:
                not_found.append({"name": name})
        else:
            uncached.append(name)

    if not uncached:
        return found, not_found

    # Fetch uncached names (already batched to <=75 by caller or by us)
    fetched_found, fetched_not_found = _fetch_collection(uncached)
    for card in fetched_found:
        cache[card["name"]] = card
        found.append(card)
    for nf in fetched_not_found:
        cache[nf["name"]] = None
        not_found.append(nf)

    _save_cache()
    fetched = len(fetched_found) + len(fetched_not_found)
    print(f"  Scryfall: fetched {fetched} cards ({len(cache)} cached)")
    return found, not_found


def named(name: str) -> dict | None:
    """Look up a single card by exact name, with disk caching.

    Returns the card object, or None if not found.
    """
    cache = _load_cache()
    if name in cache:
        return cache[name]

    _rate_limit()
    qs = urllib.parse.urlencode({"exact": name})
    req = urllib.request.Request(
        f"https://api.scryfall.com/cards/named?{qs}",
        headers=_HEADERS,
    )
    try:
        with urllib.request.urlopen(req) as resp:
            card = json.loads(resp.read())
        cache[name] = card
        _save_cache()
        return card
    except urllib.error.HTTPError:
        cache[name] = None
        _save_cache()
        return None


def search_token(token_name: str) -> str | None:
    """Search Scryfall for a token's image URL, with disk caching.

    Strips " Token" suffix, searches for the base name as a token card.
    Returns the small image URL, or None if not found.
    """
    base_name = token_name.removesuffix(" Token").removesuffix(" token").strip()
    cache_key = f"token:{base_name}"
    cache = _load_cache()
    if cache_key in cache:
        cached = cache[cache_key]
        return cached if isinstance(cached, str) else None

    _rate_limit()
    query = f'!"{base_name}" t:token'
    qs = urllib.parse.urlencode(
        {"q": query, "unique": "art", "order": "released", "dir": "desc"}
    )
    req = urllib.request.Request(
        f"https://api.scryfall.com/cards/search?{qs}",
        headers=_HEADERS,
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        cards = data.get("data", [])
        if cards:
            image_uris = cards[0].get("image_uris")
            url = image_uris.get("small") if image_uris else None
            cache[cache_key] = url
            _save_cache()
            return url
        cache[cache_key] = None
        _save_cache()
        return None
    except urllib.error.HTTPError:
        cache[cache_key] = None
        _save_cache()
        return None


def search(query: str) -> list[dict]:
    """Search Scryfall with a query string, no caching.

    Returns list of card objects (may be empty).
    """
    _rate_limit()
    qs = urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(
        f"https://api.scryfall.com/cards/search?{qs}",
        headers=_HEADERS,
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        return data.get("data", [])
    except urllib.error.HTTPError:
        return []
