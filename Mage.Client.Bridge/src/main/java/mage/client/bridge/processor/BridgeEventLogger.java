package mage.client.bridge.processor;

import java.util.UUID;

@FunctionalInterface
public interface BridgeEventLogger {
    void log(String method, UUID gameId, String summary);
}
