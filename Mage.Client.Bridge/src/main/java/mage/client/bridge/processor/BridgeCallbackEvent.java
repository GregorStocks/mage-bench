package mage.client.bridge.processor;

import mage.interfaces.callback.ClientCallback;

public record BridgeCallbackEvent(ClientCallback callback) implements BridgeProcessorMessage {
}
