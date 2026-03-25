package mage.client.bridge.processor;

import mage.client.bridge.tools.ActionResult;

import java.util.List;
import java.util.Objects;

public final class BridgeBuiltActionChoices {
    private final ActionResult result;
    private final List<Object> backingChoices;

    public BridgeBuiltActionChoices(ActionResult result, List<Object> backingChoices) {
        this.result = Objects.requireNonNull(result);
        this.backingChoices = List.copyOf(Objects.requireNonNull(backingChoices));
    }

    public ActionResult result() {
        return result;
    }

    public List<Object> backingChoices() {
        return backingChoices;
    }
}
