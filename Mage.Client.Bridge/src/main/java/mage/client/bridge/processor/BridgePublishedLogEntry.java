package mage.client.bridge.processor;

import mage.game.BridgeLogEntry;

public record BridgePublishedLogEntry(int seq, BridgeLogEntry bridgeEvent, String rendered) {

    public boolean isBridgeEvent() {
        return bridgeEvent != null;
    }
}
