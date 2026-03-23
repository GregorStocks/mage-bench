package mage.client.bridge.processor;

import java.util.List;
import java.util.Map;

public record BridgePublishedGameState(
        boolean available,
        String error,
        Long snapshotId,
        Integer turn,
        String phase,
        String step,
        String activePlayer,
        String priorityPlayer,
        List<Map<String, Object>> players,
        List<Map<String, Object>> stack,
        List<Map<String, Object>> combat,
        Integer gameSeq
) {
    public static BridgePublishedGameState unavailable(String error) {
        return new BridgePublishedGameState(
            false,
            error,
            null,
            null,
            null,
            null,
            null,
            null,
            null,
            null,
            null,
            null
        );
    }
}
