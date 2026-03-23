package mage.client.bridge.processor;

import mage.interfaces.callback.ClientCallbackMethod;
import org.apache.log4j.Logger;

import java.util.function.Consumer;

public final class BridgeRecordedActionableCallbackOutcome implements BridgeActionableCallbackOutcome {
    private final ClientCallbackMethod method;
    private final Logger logger;
    private final String username;
    private final Consumer<String> bridgeEventLogger;
    private String outcome = null;

    public BridgeRecordedActionableCallbackOutcome(
            ClientCallbackMethod method,
            Logger logger,
            String username,
            Consumer<String> bridgeEventLogger) {
        this.method = method;
        this.logger = logger;
        this.username = username;
        this.bridgeEventLogger = bridgeEventLogger;
    }

    @Override
    public void storedPendingAction(String detail) {
        record("stored_pending_action:" + detail);
    }

    @Override
    public void sentResponse(String detail) {
        record("sent_response:" + detail);
    }

    @Override
    public void verifyRecorded() {
        if (outcome == null) {
            throw new IllegalStateException(
                    "Actionable callback " + method
                    + " returned without storing a pending action or sending a response");
        }
    }

    private void record(String nextOutcome) {
        if (outcome != null) {
            throw new IllegalStateException(
                    "Actionable callback " + method
                    + " recorded multiple outcomes: " + outcome + " then " + nextOutcome);
        }
        outcome = nextOutcome;
        logger.debug("[" + username + "] Callback outcome " + method + ": " + nextOutcome);
        bridgeEventLogger.accept(method.name() + ": " + nextOutcome);
    }
}
