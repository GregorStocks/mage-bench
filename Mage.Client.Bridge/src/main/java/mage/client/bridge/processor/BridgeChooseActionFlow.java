package mage.client.bridge.processor;

import mage.client.bridge.PendingAction;
import mage.client.bridge.tools.ChooseActionTool;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;

public final class BridgeChooseActionFlow {
    private enum Phase {
        WAITING_FOR_ACTION,
        WAITING_FOR_NEXT_DECISION
    }

    private final BridgeChooseActionFlowContext context;
    private final BridgeChooseActionInput input;
    private final CompletableFuture<ChooseActionTool.Result> result = new CompletableFuture<>();

    private Phase phase = Phase.WAITING_FOR_ACTION;
    private PendingAction previousAction = null;
    private ChooseActionTool.Result partialResult = null;

    public BridgeChooseActionFlow(BridgeChooseActionFlowContext context, BridgeChooseActionInput input) {
        this.context = context;
        this.input = input;
    }

    public void start() {
        advance();
    }

    public void advance() {
        if (result.isDone()) {
            return;
        }
        if (phase == Phase.WAITING_FOR_ACTION) {
            advanceWaitingForAction();
        }
        if (phase == Phase.WAITING_FOR_NEXT_DECISION && !result.isDone()) {
            advanceWaitingForNextDecision();
        }
    }

    public boolean isDone() {
        return result.isDone();
    }

    public ChooseActionTool.Result interrupt() {
        if (result.isDone()) {
            return result.join();
        }
        ChooseActionTool.Result interrupted = context.interruptedChooseActionResult(previousAction, partialResult);
        result.complete(interrupted);
        return interrupted;
    }

    public ChooseActionTool.Result awaitResult() throws InterruptedException, ExecutionException {
        return result.get();
    }

    public void finish(ChooseActionTool.Result finalResult) {
        result.complete(finalResult);
    }

    private void advanceWaitingForAction() {
        PendingAction action = context.currentDecisionAction();
        if (action == null) {
            if (context.requestCannotContinue()) {
                result.complete(context.noPendingActionResult());
            }
            return;
        }

        previousAction = action;
        BridgeChooseActionStartResult startResult = context.applyChooseAction(input, action);
        partialResult = startResult.result();
        if (!startResult.waitForNextDecision()) {
            result.complete(partialResult);
            return;
        }

        phase = Phase.WAITING_FOR_NEXT_DECISION;
    }

    private void advanceWaitingForNextDecision() {
        PendingAction nextAction = context.currentDecisionAction();
        if (nextAction != null) {
            context.finishChooseActionWithNextDecision(partialResult, previousAction, nextAction);
            result.complete(partialResult);
            return;
        }
        if (context.requestCannotContinue()) {
            context.finishChooseActionWithoutNextDecision(partialResult, previousAction);
            result.complete(partialResult);
        }
    }
}
