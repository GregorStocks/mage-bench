"""Validate that XMage 3-char hex ID suffixes can be safely stripped from MCP tool results.

The regex ` \\[[0-9a-f]{3}\\]` matches the suffixes XMage appends to card names
(first 3 chars of the object UUID). This test validates against historical game
data that the pattern has zero false positives.
"""

import json
import re
from pathlib import Path

# Same pattern used in BridgeCallbackHandler.java (HEX_SUFFIX_PATTERN)
HEX_SUFFIX_RE = re.compile(r" \[[0-9a-f]{3}\]")

LOGS_DIR = Path.home() / ".mage-bench" / "logs"


def test_strip_basic():
    """Known good cases."""
    assert HEX_SUFFIX_RE.sub("", "Force of Will [a0b]") == "Force of Will"
    assert HEX_SUFFIX_RE.sub("", "Mountain [3d2]") == "Mountain"
    assert HEX_SUFFIX_RE.sub("", "Mishra's Bauble [aeb]") == "Mishra's Bauble"


def test_strip_multiple():
    """Multiple suffixes in one string."""
    s = "Daze [dc6] targeting Force of Will [a0b]"
    assert HEX_SUFFIX_RE.sub("", s) == "Daze targeting Force of Will"


def test_strip_mid_sentence():
    """Suffix appears mid-sentence, not just at end."""
    s = "sacrificed Mishra's Bauble [aeb] (source: Mishra's Bauble [aeb])"
    assert HEX_SUFFIX_RE.sub("", s) == "sacrificed Mishra's Bauble (source: Mishra's Bauble)"


def test_no_strip_uppercase():
    """Uppercase hex chars are not stripped (XMage always uses lowercase)."""
    assert HEX_SUFFIX_RE.sub("", "Card [ABC]") == "Card [ABC]"
    assert HEX_SUFFIX_RE.sub("", "Card [A0B]") == "Card [A0B]"


def test_no_strip_non_hex():
    """Non-hex characters are not stripped."""
    assert HEX_SUFFIX_RE.sub("", "Card [xyz]") == "Card [xyz]"
    assert HEX_SUFFIX_RE.sub("", "Card [ghj]") == "Card [ghj]"


def test_no_strip_wrong_length():
    """Wrong-length brackets are not stripped."""
    assert HEX_SUFFIX_RE.sub("", "Card [ab]") == "Card [ab]"
    assert HEX_SUFFIX_RE.sub("", "Card [abcd]") == "Card [abcd]"


def test_no_strip_no_space():
    """Without preceding space, not stripped."""
    assert HEX_SUFFIX_RE.sub("", "Card[abc]") == "Card[abc]"


def test_no_strip_mana_costs():
    """Mana costs use braces, not brackets."""
    assert HEX_SUFFIX_RE.sub("", "{U}{B}{2}") == "{U}{B}{2}"


def test_no_strip_deck_format():
    """Deck format uses [SET:NUM] — different pattern."""
    assert HEX_SUFFIX_RE.sub("", "4 [LCI:123] Lightning Bolt") == "4 [LCI:123] Lightning Bolt"


def test_passthrough_empty():
    assert HEX_SUFFIX_RE.sub("", "") == ""


def test_passthrough_no_brackets():
    assert HEX_SUFFIX_RE.sub("", "just a regular string") == "just a regular string"


def test_historical_data_no_false_positives():
    """Scan all historical LLM logs and verify every [xxx] match is a card name suffix.

    Each match should be preceded by text ending with a word character (letter,
    digit, apostrophe, or closing paren) — i.e. a card name, not a structural element.
    """
    llm_files = sorted(LOGS_DIR.glob("*/*_llm.jsonl")) if LOGS_DIR.exists() else []
    if not llm_files:
        return  # Skip on CI where historical data doesn't exist

    total_matches = 0
    false_positives: list[dict[str, str]] = []

    for llm_file in llm_files:
        for line in llm_file.read_text().splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "tool_call":
                continue
            result_str = obj.get("result", "")
            if not result_str:
                continue

            for match in HEX_SUFFIX_RE.finditer(result_str):
                total_matches += 1
                # Get preceding context (up to 50 chars before the space)
                start = max(0, match.start() - 50)
                context = result_str[start : match.start()]
                # The char immediately before the space+bracket should be a word
                # character, closing paren, or quotation mark — part of a name.
                if context and not re.search(r"[\w')\"\\]$", context):
                    false_positives.append(
                        {
                            "file": llm_file.name,
                            "match": match.group(0),
                            "context": context[-30:],
                        }
                    )

    if total_matches == 0:
        return  # Historical data exists but contains no hex suffixes to validate
    assert not false_positives, (
        f"Found {len(false_positives)} potential false positives "
        f"(out of {total_matches} total matches):\n"
        + "\n".join(f"  {fp['file']}: ...{fp['context']}{fp['match']}" for fp in false_positives[:10])
    )
