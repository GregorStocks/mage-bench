package mage.client.bridge.processor;

import java.util.Map;
import java.util.Set;

public final class BridgePublishedOracleIndex {
    private static final BridgePublishedOracleIndex EMPTY =
        new BridgePublishedOracleIndex(Map.of(), Map.of(), Set.of());

    private final Map<String, Map<String, Object>> cardsByObjectId;
    private final Map<String, Map<String, Object>> cardsByName;
    private final Set<String> knownObjectIds;

    public BridgePublishedOracleIndex(
            Map<String, Map<String, Object>> cardsByObjectId,
            Map<String, Map<String, Object>> cardsByName,
            Set<String> knownObjectIds) {
        this.cardsByObjectId = Map.copyOf(cardsByObjectId);
        this.cardsByName = Map.copyOf(cardsByName);
        this.knownObjectIds = Set.copyOf(knownObjectIds);
    }

    public static BridgePublishedOracleIndex empty() {
        return EMPTY;
    }

    public Map<String, Object> card(String objectId) {
        return cardsByObjectId.get(objectId);
    }

    public Map<String, Object> cardByName(String name) {
        return cardsByName.get(name);
    }

    public boolean knowsObjectId(String objectId) {
        return knownObjectIds.contains(objectId);
    }
}
