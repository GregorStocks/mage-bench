package mage.client.bridge.processor;

public interface BridgeActionableCallbackOutcome {
    void storedPendingAction(String detail);

    void sentResponse(String detail);

    void verifyRecorded();
}
