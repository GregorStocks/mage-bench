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
from pathlib import Path

from magebench.common import http_utils

_HEADERS = {
    "User-Agent": "mage-bench/1.0",
    "Accept": "application/json",
}
_SCRYFALL_HOSTS = frozenset({"api.scryfall.com"})

_CACHE_PATH = Path.home() / ".mage-bench" / "scryfall-cache.json"

# Track last request time for rate limiting
_last_request_time: float = 0.0

# In-memory cache, lazily loaded from disk
_cache: dict[str, dict | str | None] | None = None


def _dict_list(value: object, *, context: str) -> list[dict]:
    assert isinstance(value, list), f"{context}: expected list, got {type(value).__name__}"
    items: list[dict] = []
    for index, item in enumerate(value):
        assert isinstance(item, dict), f"{context}[{index}]: expected object, got {type(item).__name__}"
        items.append(item)
    return items


def _rate_limit() -> None:
    """Ensure at least 100ms between Scryfall API requests."""
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if _last_request_time > 0 and elapsed < 0.1:
        time.sleep(0.1 - elapsed)
    _last_request_time = time.monotonic()


def _load_cache() -> dict[str, dict | str | None]:
    """Load cache from disk, or return empty dict."""
    global _cache
    if _cache is None:
        if _CACHE_PATH.exists():
            cached = json.loads(_CACHE_PATH.read_text())
            assert isinstance(cached, dict), f"{_CACHE_PATH}: expected JSON object"
            _cache = cached
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
    data = json.loads(
        http_utils.fetch_https_bytes(
            "https://api.scryfall.com/cards/collection",
            allowed_hosts=_SCRYFALL_HOSTS,
            data=body,
            headers={**_HEADERS, "Content-Type": "application/json"},
        )
    )
    assert isinstance(data, dict), "Scryfall collection returned non-object payload"
    found_data = data.get("data")
    not_found_data = data.get("not_found")
    return (
        _dict_list(found_data, context="Scryfall collection data") if found_data is not None else [],
        _dict_list(not_found_data, context="Scryfall collection not_found") if not_found_data is not None else [],
    )


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
            val = cache[name]
            assert val is None or isinstance(val, dict), (
                f"Scryfall card cache entry for {name!r} must be an object or null, got {val!r}"
            )
            if isinstance(val, dict):
                found.append(val)
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
        cached = cache[name]
        assert cached is None or isinstance(cached, dict), (
            f"Scryfall card cache entry for {name!r} must be an object or null, got {cached!r}"
        )
        return cached

    _rate_limit()
    qs = urllib.parse.urlencode({"exact": name})
    try:
        card = json.loads(
            http_utils.fetch_https_bytes(
                f"https://api.scryfall.com/cards/named?{qs}",
                allowed_hosts=_SCRYFALL_HOSTS,
                headers=_HEADERS,
            )
        )
        assert isinstance(card, dict), f"Scryfall named({name!r}) returned non-object payload"
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
        assert cached is None or isinstance(cached, str), (
            f"Scryfall token cache entry for {cache_key!r} must be a string or null, got {cached!r}"
        )
        return cached

    _rate_limit()
    query = f'!"{base_name}" t:token'
    qs = urllib.parse.urlencode({"q": query, "unique": "art", "order": "released", "dir": "desc"})
    try:
        data = json.loads(
            http_utils.fetch_https_bytes(
                f"https://api.scryfall.com/cards/search?{qs}",
                allowed_hosts=_SCRYFALL_HOSTS,
                headers=_HEADERS,
            )
        )
        assert isinstance(data, dict), f"Scryfall token search for {token_name!r} returned non-object payload"
        token_data = data.get("data")
        cards = (
            _dict_list(token_data, context=f"Scryfall token search for {token_name!r}")
            if token_data is not None
            else []
        )
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


def extract_oracle_fields(card: dict) -> dict:
    """Extract the fields we need from a Scryfall card object."""
    fields: dict = {
        "name": card["name"],
    }
    mana_cost = card.get("mana_cost")
    if mana_cost is not None:
        fields["mana_cost"] = mana_cost
    type_line = card.get("type_line")
    if type_line is not None:
        fields["type_line"] = type_line
    oracle_text = card.get("oracle_text")
    if oracle_text is not None:
        fields["oracle_text"] = oracle_text
    if card.get("power") is not None:
        fields["power"] = card["power"]
        fields["toughness"] = card["toughness"]
    if card.get("loyalty") is not None:
        fields["loyalty"] = card["loyalty"]
    if card.get("card_faces"):
        fields["card_faces"] = [extract_oracle_fields(face) for face in card["card_faces"]]
    return fields


def get_oracle_texts(names: list[str]) -> dict[str, dict]:
    """Get oracle texts for cards via Scryfall (cached on disk).

    Returns {card_name: oracle_fields} for all names that resolved.
    """
    result: dict[str, dict] = {}
    for i in range(0, len(names), 75):
        batch = names[i : i + 75]
        found, _not_found = collection(batch)
        for card in found:
            result[card["name"]] = extract_oracle_fields(card)
    return result


def resolve_cards(
    names: list[str],
    pre_resolved: dict[str, tuple[str, str]] | None = None,
    preferred_sets: set[str] | None = None,
) -> dict[str, tuple[str, str]]:
    """Resolve card names to (set_code, collector_number) via Scryfall.

    Args:
        names: Card names to resolve.
        pre_resolved: Already-resolved cards to skip (e.g. from XMage set file).
        preferred_sets: If provided, re-resolve cards not in these sets by
            searching for a printing in a preferred set.

    Returns dict mapping card name to (set_code, collector_number).
    """
    resolved: dict[str, tuple[str, str]] = {}
    remaining: set[str] = set(names)

    # Step 1: Use pre-resolved cards
    if pre_resolved:
        for name in list(remaining):
            if name in pre_resolved:
                resolved[name] = pre_resolved[name]
                remaining.discard(name)

    if not remaining:
        return resolved

    # Step 2: Batch collection lookup
    name_list = sorted(remaining)
    print(f"Resolving {len(name_list)} cards via Scryfall...")
    for i in range(0, len(name_list), 75):
        batch = name_list[i : i + 75]
        found, not_found = collection(batch)
        for card in found:
            resolved[card["name"]] = (card["set"].upper(), card["collector_number"])
        for nf in not_found:
            print(f"  WARNING: not found in collection: {nf['name']}")

    # Step 3: Individual fallback for missing cards (split card handling)
    missing = remaining - set(resolved.keys())
    for name in sorted(missing):
        lookup = named(name)
        if lookup is None and " // " in name:
            lookup = named(name.split(" // ")[0])
        if lookup is not None:
            resolved[name] = (lookup["set"].upper(), lookup["collector_number"])
        else:
            print(f"  ERROR: card not found at all: {name}")

    # Step 4: Re-resolve cards not in preferred sets
    if preferred_sets:
        recheck = [n for n in resolved if resolved[n][0] not in preferred_sets]
        if recheck:
            print(f"Re-resolving {len(recheck)} cards to find preferred set printings...")
            for name in recheck:
                search_name = name.split(" // ")[0] if " // " in name else name
                for pref_set in sorted(preferred_sets):
                    results = search(f'!"{search_name}" set:{pref_set.lower()}')
                    if results:
                        resolved[name] = (
                            results[0]["set"].upper(),
                            results[0]["collector_number"],
                        )
                        break

    return resolved


def search(query: str) -> list[dict]:
    """Search Scryfall with a query string, no caching.

    Returns list of card objects (may be empty).
    """
    _rate_limit()
    qs = urllib.parse.urlencode({"q": query})
    try:
        data = json.loads(
            http_utils.fetch_https_bytes(
                f"https://api.scryfall.com/cards/search?{qs}",
                allowed_hosts=_SCRYFALL_HOSTS,
                headers=_HEADERS,
            )
        )
        assert isinstance(data, dict), f"Scryfall search({query!r}) returned non-object payload"
        search_data = data.get("data")
        if search_data is not None:
            return _dict_list(search_data, context=f"Scryfall search({query!r})")
        return []
    except urllib.error.HTTPError:
        return []
