"""Regression tests for golden cleanup concede timing."""

from __future__ import annotations

import re

from tests.golden_helpers import DEFENSIVE_CONCEDE_TIMEOUT_SECONDS, REPO_ROOT

BRIDGE_CALLBACK_HANDLER = (
    REPO_ROOT
    / "Mage.Client.Bridge"
    / "src"
    / "main"
    / "java"
    / "mage"
    / "client"
    / "bridge"
    / "BridgeCallbackHandler.java"
)


def _java_keepalive_concede_wait_seconds() -> int:
    source = BRIDGE_CALLBACK_HANDLER.read_text(encoding="utf-8")
    match = re.search(r"KEEPALIVE_CONCEDE_WAIT_SECONDS\s*=\s*(\d+)\s*;", source)
    assert match is not None, "BridgeCallbackHandler must define KEEPALIVE_CONCEDE_WAIT_SECONDS"
    return int(match.group(1))


def test_defensive_concede_timeout_exceeds_java_keepalive_wait() -> None:
    java_wait = _java_keepalive_concede_wait_seconds()
    assert java_wait + 5 <= DEFENSIVE_CONCEDE_TIMEOUT_SECONDS
