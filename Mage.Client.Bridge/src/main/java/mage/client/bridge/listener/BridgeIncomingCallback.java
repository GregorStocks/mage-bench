package mage.client.bridge.listener;

import mage.client.bridge.BridgeCallbackHandler;
import mage.interfaces.callback.ClientCallback;

record BridgeIncomingCallback(
        BridgeCallbackHandler handler,
        ClientCallback callback) implements BridgeCallbackListenerMessage {
}
