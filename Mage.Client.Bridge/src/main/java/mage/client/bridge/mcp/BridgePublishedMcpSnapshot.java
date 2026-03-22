package mage.client.bridge.mcp;

import mage.client.bridge.processor.BridgeChatLogEntry;
import mage.game.BridgeLogEntry;

import java.util.List;

record BridgePublishedMcpSnapshot(
        boolean actionPending,
        BridgePublishedGameState gameState,
        List<BridgeLogEntry> bridgeEvents,
        List<BridgeChatLogEntry> chatLog
) {
    static BridgePublishedMcpSnapshot empty() {
        return new BridgePublishedMcpSnapshot(
            false,
            BridgePublishedGameState.unavailable("No game state available yet"),
            List.of(),
            List.of()
        );
    }

    int nextBridgeEventCursor() {
        return bridgeEvents.isEmpty() ? 0 : bridgeEvents.get(bridgeEvents.size() - 1).index() + 1;
    }
}
