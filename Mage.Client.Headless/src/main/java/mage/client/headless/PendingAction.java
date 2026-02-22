package mage.client.headless;

import mage.interfaces.callback.ClientCallbackMethod;

import java.util.UUID;

/**
 * Encapsulates a pending game action that requires a response.
 * Used in MCP mode to track what action the external client needs to handle.
 */
public record PendingAction(UUID gameId, ClientCallbackMethod method, Object data, String message, int gameSeq) {

    @Override
    public String toString() {
        return "PendingAction{" +
                "gameId=" + gameId +
                ", method=" + method +
                ", message='" + message + '\'' +
                '}';
    }
}
