package mage.client.bridge.processor;

import java.util.List;

public record BridgePublishedGameLog(List<BridgePublishedLogEntry> entries, int nextCursor) {

    public static BridgePublishedGameLog empty() {
        return new BridgePublishedGameLog(List.of(), 0);
    }

    public int firstCursor() {
        return entries.isEmpty() ? nextCursor : entries.get(0).seq();
    }
}
