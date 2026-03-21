package mage.client.bridge.processor;

import mage.interfaces.callback.ClientCallbackMethod;

import java.util.UUID;

public interface BridgeCallbackDispatcherContext {
    String nonCurrentGameCallbackIgnoreReason(UUID callbackGameId, ClientCallbackMethod method);

    void logCallbackReceived(UUID callbackGameId, ClientCallbackMethod method, String ignoreReason);

    boolean shouldIgnoreNonCurrentGameCallback(UUID callbackGameId, ClientCallbackMethod method, String ignoreReason);

    void recordCallbackArrival(ClientCallbackMethod method);

    BridgeActionableCallbackOutcome createActionableOutcome(ClientCallbackMethod method);

    boolean shouldLogBridgeEvents();

    String buildBridgeStateSummary();

    void logBridgeEvent(ClientCallbackMethod method, UUID gameId, String summary);

    void storePendingAction(UUID gameId, ClientCallbackMethod method, Object data);

    void handleStartGame(UUID gameId, Object data);

    void handleGameInit(Object data);

    void logGameState(Object data);

    void handleGameOver(UUID gameId, Object data);

    void handleEndGameInfo(UUID gameId);

    void handleChatMessage(Object data);

    void logEvent(ClientCallbackMethod method, Object data);

    void handleUserRequestDialog(Object data);

    void logUnhandledCallback(ClientCallbackMethod method);

    void handleProcessorCallbackException(ClientCallbackMethod method, Exception e, boolean actionable);
}
