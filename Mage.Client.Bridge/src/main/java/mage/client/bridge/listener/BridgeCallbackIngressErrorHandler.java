package mage.client.bridge.listener;

import mage.interfaces.callback.ClientCallbackMethod;

@FunctionalInterface
public interface BridgeCallbackIngressErrorHandler {
    void handleCallbackIngressException(ClientCallbackMethod method, Exception exception, boolean actionable);
}
