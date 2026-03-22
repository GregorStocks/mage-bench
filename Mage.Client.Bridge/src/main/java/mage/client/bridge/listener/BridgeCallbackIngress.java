package mage.client.bridge.listener;

import mage.client.bridge.processor.BridgeCallbackEvent;
import mage.interfaces.callback.ClientCallback;
import mage.interfaces.callback.ClientCallbackMethod;

import java.util.function.Consumer;
import java.util.function.Predicate;

public final class BridgeCallbackIngress {
    private final Predicate<ClientCallbackMethod> actionableCallback;
    private final Consumer<BridgeCallbackEvent> enqueueCallback;
    private final BridgeCallbackIngressErrorHandler errorHandler;

    public BridgeCallbackIngress(
            Predicate<ClientCallbackMethod> actionableCallback,
            Consumer<BridgeCallbackEvent> enqueueCallback,
            BridgeCallbackIngressErrorHandler errorHandler) {
        this.actionableCallback = actionableCallback;
        this.enqueueCallback = enqueueCallback;
        this.errorHandler = errorHandler;
    }

    public void handleCallback(ClientCallback callback) {
        ClientCallbackMethod method = callback.getMethod();
        try {
            callback.decompressData();
            enqueueCallback.accept(new BridgeCallbackEvent(
                callback.getObjectId(),
                method,
                callback.getData()
            ));
        } catch (Exception e) {
            errorHandler.handleCallbackIngressException(method, e, actionableCallback.test(method));
        }
    }
}
