package mage.client.bridge.processor;

import java.util.List;
import java.util.Map;

public record BridgeProjectedActionContext(
        boolean available,
        String context,
        List<Map<String, Object>> board,
        List<Map<String, Object>> stack,
        List<Map<String, Object>> combat,
        Integer untappedLands,
        Integer landDropsUsed,
        Integer gameSeq
) {
    public static BridgeProjectedActionContext empty() {
        return new BridgeProjectedActionContext(
            false,
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
