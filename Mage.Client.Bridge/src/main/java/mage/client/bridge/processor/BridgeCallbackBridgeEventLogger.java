package mage.client.bridge.processor;

import java.util.UUID;

@FunctionalInterface
public interface BridgeCallbackBridgeEventLogger {
    void logBridgeEvent(String method, UUID gameId, String summary);
}
