package mage.client.bridge.processor;

import mage.client.bridge.BridgeCallbackHandler;
import mage.client.bridge.tools.ChooseActionTool;

import java.util.function.Function;

public final class BridgeChooseActionFlowManager {
    private final BridgeDecisionState decisionState;
    private final BridgeChooseActionFlowContext context;
    private final Function<String, ChooseActionTool.Result> deliveryErrorResultFactory;

    public BridgeChooseActionFlowManager(
            BridgeDecisionState decisionState,
            BridgeChooseActionFlowContext context,
            Function<String, ChooseActionTool.Result> deliveryErrorResultFactory) {
        this.decisionState = decisionState;
        this.context = context;
        this.deliveryErrorResultFactory = deliveryErrorResultFactory;
    }

    public BridgeChooseActionFlow startPendingFlow(BridgeChooseActionInput input) {
        BridgeChooseActionFlow flow = new BridgeChooseActionFlow(context, input);
        decisionState.setPendingChooseActionFlow(flow);
        try {
            flow.start();
        } catch (BridgeCallbackHandler.ResponseDeliveryException e) {
            flow.finish(deliveryErrorResultFactory.apply(e.getMessage()));
        } catch (RuntimeException e) {
            decisionState.clearPendingChooseActionFlowIfCurrent(flow);
            throw e;
        }
        if (flow.isDone()) {
            decisionState.clearPendingChooseActionFlowIfCurrent(flow);
        }
        return flow;
    }

    public void advancePendingFlow() {
        BridgeChooseActionFlow flow = decisionState.pendingChooseActionFlow();
        if (flow == null) {
            return;
        }
        try {
            flow.advance();
        } catch (BridgeCallbackHandler.ResponseDeliveryException e) {
            flow.finish(deliveryErrorResultFactory.apply(e.getMessage()));
        }
        if (flow.isDone()) {
            decisionState.clearPendingChooseActionFlowIfCurrent(flow);
        }
    }

    public void tickPendingFlow(BridgeChooseActionFlow flow) {
        if (decisionState.pendingChooseActionFlow() != flow) {
            return;
        }
        advancePendingFlow();
    }

    public ChooseActionTool.Result interruptFlow(BridgeChooseActionFlow flow) {
        try {
            return flow.interrupt();
        } finally {
            decisionState.clearPendingChooseActionFlowIfCurrent(flow);
        }
    }

    public ChooseActionTool.Result finishAfterProcessorShutdown(BridgeChooseActionFlow flow) {
        try {
            return flow.finishAfterProcessorShutdown();
        } finally {
            decisionState.clearPendingChooseActionFlowIfCurrent(flow);
        }
    }
}
