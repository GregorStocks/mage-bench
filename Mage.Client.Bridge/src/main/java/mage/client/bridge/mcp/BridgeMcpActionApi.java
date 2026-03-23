package mage.client.bridge.mcp;

import mage.client.bridge.processor.BridgeActionCommandService;
import mage.client.bridge.processor.BridgeChooseActionFlow;
import mage.client.bridge.processor.BridgeChooseActionInput;
import mage.client.bridge.processor.BridgeCommand;
import mage.client.bridge.processor.BridgeConcedeFlow;
import mage.client.bridge.processor.BridgePassPriorityFlow;
import mage.client.bridge.processor.BridgeProcessor;
import mage.client.bridge.tools.ActionResult;
import mage.client.bridge.tools.ChooseActionTool;

import java.util.Map;
import java.util.concurrent.ExecutionException;
import java.util.function.Supplier;

public final class BridgeMcpActionApi {
    private final BridgeProcessor processor;
    private final BridgeActionCommandService actionCommandService;

    public BridgeMcpActionApi(
            BridgeProcessor processor,
            BridgeActionCommandService actionCommandService) {
        this.processor = processor;
        this.actionCommandService = actionCommandService;
    }

    public Map<String, Object> executeDefaultAction() {
        return processor.submit(BridgeCommand.of(actionCommandService::executeDefaultAction));
    }

    public ChooseActionTool.Result chooseAction(
            Integer index,
            String id,
            Boolean answer,
            Integer amount,
            int[] amounts,
            Integer pile,
            String text,
            String[] manaPlanArray,
            Boolean autoTap,
            String[] attackers,
            String[] blockersArray) {
        BridgeChooseActionInput input = new BridgeChooseActionInput(
            index,
            id,
            answer,
            amount,
            amounts,
            pile,
            text,
            manaPlanArray,
            autoTap,
            attackers,
            blockersArray
        );
        BridgeChooseActionFlow flow =
            submitProcessorCommandPreservingInterrupt(() -> actionCommandService.startChooseActionFlow(input));
        if (flow == null) {
            return submitProcessorCommandPreservingInterrupt(actionCommandService::chooseActionAlreadyPendingResult);
        }

        try {
            return flow.awaitResult();
        } catch (InterruptedException e) {
            return cancelChooseActionFlowAfterCallerInterrupt(flow);
        } catch (ExecutionException e) {
            Throwable cause = e.getCause();
            if (cause instanceof RuntimeException runtimeException) {
                throw runtimeException;
            }
            throw new IllegalStateException("chooseAction request failed", cause);
        }
    }

    public String sendChatMessage(String message) {
        return processor.submit(BridgeCommand.of(() -> actionCommandService.sendChatMessage(message)));
    }

    public boolean concede() {
        BridgeConcedeFlow flow = processor.submit(BridgeCommand.of(actionCommandService::startConcedeFlow));
        try {
            return flow.awaitResult();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Interrupted while waiting for concede result", e);
        } catch (ExecutionException e) {
            Throwable cause = e.getCause();
            if (cause instanceof RuntimeException runtimeException) {
                throw runtimeException;
            }
            throw new IllegalStateException("concede request failed", cause);
        }
    }

    public ActionResult passPriority(String until, Long boardCursorParam) {
        BridgePassPriorityFlow flow = submitProcessorCommandPreservingInterrupt(
            () -> actionCommandService.startPassPriorityFlow(until, boardCursorParam)
        );

        if (flow == null) {
            return submitProcessorCommandPreservingInterrupt(actionCommandService::passPriorityAlreadyPendingResult);
        }

        try {
            return flow.awaitResult();
        } catch (InterruptedException e) {
            return cancelPassPriorityFlowAfterCallerInterrupt(flow);
        } catch (ExecutionException e) {
            Throwable cause = e.getCause();
            if (cause instanceof RuntimeException runtimeException) {
                throw runtimeException;
            }
            throw new IllegalStateException("passPriority request failed", cause);
        }
    }

    public ActionResult waitAndGetChoices(String until, Long boardCursorParam) {
        return passPriority(until, boardCursorParam);
    }

    private ChooseActionTool.Result cancelChooseActionFlowAfterCallerInterrupt(BridgeChooseActionFlow flow) {
        try {
            return submitProcessorCommandPreservingInterrupt(() -> actionCommandService.cancelChooseActionFlow(flow));
        } catch (IllegalStateException e) {
            return actionCommandService.cancelChooseActionFlow(flow);
        } finally {
            Thread.currentThread().interrupt();
        }
    }

    private ActionResult cancelPassPriorityFlowAfterCallerInterrupt(BridgePassPriorityFlow flow) {
        try {
            return submitProcessorCommandPreservingInterrupt(() -> actionCommandService.cancelPassPriorityFlow(flow));
        } catch (IllegalStateException e) {
            return actionCommandService.cancelPassPriorityFlow(flow);
        } finally {
            Thread.currentThread().interrupt();
        }
    }

    private <T> T submitProcessorCommandPreservingInterrupt(Supplier<T> supplier) {
        return processor.submitPreservingInterrupt(BridgeCommand.of(supplier));
    }
}
