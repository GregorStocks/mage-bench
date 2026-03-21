package mage.client.bridge.processor;

import mage.client.bridge.tools.ChooseActionTool;

public record BridgeChooseActionStartResult(
    ChooseActionTool.Result result,
    boolean waitForNextDecision
) {
}
