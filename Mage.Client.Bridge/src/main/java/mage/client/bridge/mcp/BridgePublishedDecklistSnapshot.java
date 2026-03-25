package mage.client.bridge.mcp;

import java.util.Map;
import java.util.Set;

public record BridgePublishedDecklistSnapshot(
    Map<String, Object> response,
    Set<String> creatureTypes
) {
}
