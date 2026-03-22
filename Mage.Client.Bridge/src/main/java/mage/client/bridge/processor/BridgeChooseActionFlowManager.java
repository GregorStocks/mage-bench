package mage.client.bridge.processor;

import mage.client.bridge.BridgeCallbackHandler;
import mage.client.bridge.tools.ChooseActionTool;

import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.Function;

public final class BridgeChooseActionFlowManager {
    private static final long FLOW_TICK_INTERVAL_MS = 200;

    private final BridgeProcessor processor;
    private final BridgeDecisionState decisionState;
    private final BridgeChooseActionFlowContext context;
    private final Function<String, ChooseActionTool.Result> deliveryErrorResultFactory;
    private final ScheduledExecutorService scheduler;
    private final Object tickLock = new Object();
    private ScheduledFuture<?> scheduledTick = null;
    private BridgeChooseActionFlow scheduledTickFlow = null;

    public BridgeChooseActionFlowManager(
            BridgeProcessor processor,
            String username,
            BridgeDecisionState decisionState,
            BridgeChooseActionFlowContext context,
            Function<String, ChooseActionTool.Result> deliveryErrorResultFactory) {
        this.processor = processor;
        this.decisionState = decisionState;
        this.context = context;
        this.deliveryErrorResultFactory = deliveryErrorResultFactory;
        this.scheduler = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread thread = new Thread(r, "bridge-choose-action-ticker-" + username);
            thread.setDaemon(true);
            return thread;
        });
    }

    public BridgeChooseActionFlow startPendingFlow(BridgeChooseActionInput input) {
        cancelScheduledTick();
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
        } else if (decisionState.pendingChooseActionFlow() == flow) {
            scheduleTicks(flow);
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
        cancelScheduledTickIfInactive(flow);
    }

    public ChooseActionTool.Result cancelFlow(BridgeChooseActionFlow flow) {
        try {
            return flow.cancel();
        } finally {
            cancelScheduledTickIfCurrent(flow);
            decisionState.clearPendingChooseActionFlowIfCurrent(flow);
        }
    }

    public void shutdown() {
        cancelScheduledTick();
        scheduler.shutdownNow();
    }

    private void scheduleTicks(BridgeChooseActionFlow flow) {
        synchronized (tickLock) {
            cancelScheduledTickLocked();
            scheduledTickFlow = flow;
            scheduledTick = scheduler.scheduleAtFixedRate(
                () -> tickFromScheduler(flow),
                FLOW_TICK_INTERVAL_MS,
                FLOW_TICK_INTERVAL_MS,
                TimeUnit.MILLISECONDS
            );
        }
    }

    private void tickFromScheduler(BridgeChooseActionFlow flow) {
        if (flow.isDone() || decisionState.pendingChooseActionFlow() != flow) {
            cancelScheduledTickIfCurrent(flow);
            return;
        }
        try {
            processor.submit(BridgeCommand.of(() -> {
                tickPendingFlow(flow);
                return null;
            }));
        } catch (IllegalStateException e) {
            if (isProcessorUnavailable(e)) {
                cancelScheduledTickIfCurrent(flow);
                return;
            }
            failFlow(flow, e);
        } catch (RuntimeException e) {
            failFlow(flow, e);
        }
    }

    private void tickPendingFlow(BridgeChooseActionFlow flow) {
        if (decisionState.pendingChooseActionFlow() != flow) {
            cancelScheduledTickIfCurrent(flow);
            return;
        }
        advancePendingFlow();
    }

    private void failFlow(BridgeChooseActionFlow flow, RuntimeException e) {
        try {
            flow.fail(e);
        } finally {
            cancelScheduledTickIfCurrent(flow);
            decisionState.clearPendingChooseActionFlowIfCurrent(flow);
        }
    }

    private void cancelScheduledTickIfInactive(BridgeChooseActionFlow flow) {
        if (flow.isDone() || decisionState.pendingChooseActionFlow() != flow) {
            cancelScheduledTickIfCurrent(flow);
            decisionState.clearPendingChooseActionFlowIfCurrent(flow);
        }
    }

    private void cancelScheduledTickIfCurrent(BridgeChooseActionFlow flow) {
        synchronized (tickLock) {
            if (scheduledTickFlow == flow) {
                cancelScheduledTickLocked();
            }
        }
    }

    private void cancelScheduledTick() {
        synchronized (tickLock) {
            cancelScheduledTickLocked();
        }
    }

    private void cancelScheduledTickLocked() {
        if (scheduledTick != null) {
            scheduledTick.cancel(false);
            scheduledTick = null;
        }
        scheduledTickFlow = null;
    }

    private static boolean isProcessorUnavailable(IllegalStateException e) {
        return "Bridge processor is shut down".equals(e.getMessage())
            || "Interrupted while waiting for bridge processor".equals(e.getMessage());
    }
}
