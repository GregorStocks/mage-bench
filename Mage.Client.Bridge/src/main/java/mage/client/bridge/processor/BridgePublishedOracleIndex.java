package mage.client.bridge.processor;

import java.util.Map;
import java.util.Set;

public final class BridgePublishedOracleIndex {
    private static final BridgePublishedOracleIndex EMPTY =
        new BridgePublishedOracleIndex(Map.of(), Set.of());

    private final Map<String, Map<String, Object>> cardsByObjectId;
    private final Set<String> knownObjectIds;

    public BridgePublishedOracleIndex(
            Map<String, Map<String, Object>> cardsByObjectId,
            Set<String> knownObjectIds) {
        this.cardsByObjectId = Map.copyOf(cardsByObjectId);
        this.knownObjectIds = Set.copyOf(knownObjectIds);
    }

    public static BridgePublishedOracleIndex empty() {
        return EMPTY;
    }

    public Map<String, Object> card(String objectId) {
        return cardsByObjectId.get(objectId);
    }

    public boolean knowsObjectId(String objectId) {
        return knownObjectIds.contains(objectId);
    }
}
