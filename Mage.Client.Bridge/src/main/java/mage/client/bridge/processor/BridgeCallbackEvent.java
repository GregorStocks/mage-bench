package mage.client.bridge.processor;

import mage.game.BridgeLogEntry;
import mage.interfaces.callback.ClientCallbackMethod;

import java.util.List;
import java.util.UUID;

public record BridgeCallbackEvent(UUID objectId, ClientCallbackMethod method, Object data, List<BridgeLogEntry> bridgeEvents)
    implements BridgeProcessorMessage {
}
