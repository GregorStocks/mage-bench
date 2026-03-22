package mage.client.bridge.processor;

import mage.client.bridge.BridgeCallbackHandler;
import mage.client.bridge.tools.ActionResult;

public final class BridgePassPriorityFlowManager {
    private final BridgeDecisionState decisionState;
    private final BridgePassPriorityFlowContext context;

    public BridgePassPriorityFlowManager(
            BridgeDecisionState decisionState,
            BridgePassPriorityFlowContext context) {
        this.decisionState = decisionState;
        this.context = context;
    }

    public BridgePassPriorityFlow startPendingFlow(String until, Long boardCursorParam) {
        BridgePassPriorityFlow flow = new BridgePassPriorityFlow(context, until, boardCursorParam);
        decisionState.setPendingPassPriorityFlow(flow);
        try {
            flow.start();
        } catch (BridgeCallbackHandler.ResponseDeliveryException e) {
            flow.finishWithDeliveryError(e.getMessage());
        } catch (RuntimeException e) {
            decisionState.clearPendingPassPriorityFlowIfCurrent(flow);
            throw e;
        }
        return flow;
    }

    public void advancePendingFlow() {
        BridgePassPriorityFlow flow = decisionState.pendingPassPriorityFlow();
        if (flow == null) {
            return;
        }
        try {
            flow.advance();
        } catch (BridgeCallbackHandler.ResponseDeliveryException e) {
            flow.finishWithDeliveryError(e.getMessage());
        }
    }

    public void tickPendingFlow(BridgePassPriorityFlow flow) {
        if (decisionState.pendingPassPriorityFlow() != flow) {
            return;
        }
        try {
            flow.tick();
        } catch (BridgeCallbackHandler.ResponseDeliveryException e) {
            flow.finishWithDeliveryError(e.getMessage());
        }
    }

    public ActionResult interruptFlow(BridgePassPriorityFlow flow) {
        return flow.interrupt();
    }
}
