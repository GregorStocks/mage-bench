package mage.client.bridge.mcp;

import mage.client.bridge.processor.BridgePublishedGameLog;

record BridgePublishedMcpSnapshot(
        BridgePublishedActionChoices actionChoices,
        BridgePublishedGameState gameState,
        BridgePublishedGameLog gameLog
) {
    static BridgePublishedMcpSnapshot empty() {
        return new BridgePublishedMcpSnapshot(
            BridgePublishedActionChoices.empty(),
            BridgePublishedGameState.unavailable("No game state available yet"),
            BridgePublishedGameLog.empty()
        );
    }
}
