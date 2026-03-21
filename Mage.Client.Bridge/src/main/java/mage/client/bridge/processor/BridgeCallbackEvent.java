package mage.client.bridge.processor;

import mage.interfaces.callback.ClientCallbackMethod;

import java.util.UUID;

public record BridgeCallbackEvent(UUID objectId, ClientCallbackMethod method, Object data)
    implements BridgeProcessorMessage {
}
