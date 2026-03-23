package mage.client.bridge.processor;

public record BridgePublishedQuerySnapshot(
        BridgePublishedActionChoices actionChoices,
        BridgePublishedGameState gameState,
        BridgePublishedGameLog gameLog
) {
    public static BridgePublishedQuerySnapshot empty() {
        return new BridgePublishedQuerySnapshot(
            BridgePublishedActionChoices.empty(),
            BridgePublishedGameState.unavailable("No game state available yet"),
            BridgePublishedGameLog.empty()
        );
    }
}
