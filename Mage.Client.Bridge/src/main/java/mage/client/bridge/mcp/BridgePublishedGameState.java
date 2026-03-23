package mage.client.bridge.mcp;

import java.util.List;
import java.util.Map;

record BridgePublishedGameState(
        boolean available,
        String error,
        Long cursor,
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
    static BridgePublishedGameState unavailable(String error) {
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
