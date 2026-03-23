package mage.client.bridge.processor;

public final class BridgeProcessorState {
    private final BridgeDecisionState decisionState = new BridgeDecisionState();
    private final BridgeGameState gameState = new BridgeGameState();
    private final BridgeCursorState cursorState = new BridgeCursorState();
    private final BridgeInteractionState interactionState = new BridgeInteractionState();
    private final BridgeGameLogState gameLogState = new BridgeGameLogState();

    public BridgeDecisionState decisionState() {
        return decisionState;
    }

    public BridgeGameState gameState() {
        return gameState;
    }

    public BridgeCursorState cursorState() {
        return cursorState;
    }

    public BridgeInteractionState interactionState() {
        return interactionState;
    }

    public BridgeGameLogState gameLogState() {
        return gameLogState;
    }

    public void reset() {
        gameState.resetProcessorState();
        decisionState.reset();
        gameLogState.reset();
        cursorState.reset();
        interactionState.resetRuntimeState();
    }
}
