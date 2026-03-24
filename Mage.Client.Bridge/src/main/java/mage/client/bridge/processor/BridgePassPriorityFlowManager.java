package mage.client.bridge.processor;

import mage.client.bridge.tools.ActionResult;

import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

public final class BridgePassPriorityFlowManager {
    private static final long FLOW_TICK_INTERVAL_MS = 200;

    private final BridgeProcessor processor;
    private final BridgeDecisionState decisionState;
    private final BridgePassPriorityFlowContext context;
    private final ScheduledExecutorService scheduler;
    private final Object tickLock = new Object();
    private ScheduledFuture<?> scheduledTick = null;
    private BridgePassPriorityFlow scheduledTickFlow = null;

    public BridgePassPriorityFlowManager(
            BridgeProcessor processor,
            String username,
            BridgeDecisionState decisionState,
            BridgePassPriorityFlowContext context) {
        this.processor = processor;
        this.decisionState = decisionState;
        this.context = context;
        this.scheduler = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread thread = new Thread(r, "bridge-pass-priority-ticker-" + username);
            thread.setDaemon(true);
            return thread;
        });
    }

    public BridgePassPriorityFlow startPendingFlow(String until, Long boardCursorParam) {
        cancelScheduledTick();
        BridgePassPriorityFlow flow = new BridgePassPriorityFlow(context, until, boardCursorParam);
        decisionState.setPendingPassPriorityFlow(flow);
        try {
            flow.start();
        } catch (BridgeResponseDeliveryException e) {
            flow.finishWithDeliveryError(e.getMessage());
        } catch (RuntimeException e) {
            decisionState.clearPendingPassPriorityFlowIfCurrent(flow);
            throw e;
        }
        if (!flow.isDone() && decisionState.pendingPassPriorityFlow() == flow) {
            scheduleTicks(flow);
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
        } catch (BridgeResponseDeliveryException e) {
            flow.finishWithDeliveryError(e.getMessage());
        }
        cancelScheduledTickIfInactive(flow);
    }

    public void tickPendingFlow(BridgePassPriorityFlow flow) {
        if (decisionState.pendingPassPriorityFlow() != flow) {
            cancelScheduledTickIfCurrent(flow);
            return;
        }
        try {
            flow.tick();
        } catch (BridgeResponseDeliveryException e) {
            flow.finishWithDeliveryError(e.getMessage());
        }
        cancelScheduledTickIfInactive(flow);
    }

    public ActionResult cancelFlow(BridgePassPriorityFlow flow) {
        try {
            return flow.cancel();
        } finally {
            cancelScheduledTickIfCurrent(flow);
        }
    }

    public void shutdown() {
        cancelScheduledTick();
        scheduler.shutdownNow();
    }

    private void scheduleTicks(BridgePassPriorityFlow flow) {
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

    private void tickFromScheduler(BridgePassPriorityFlow flow) {
        if (flow.isDone() || decisionState.pendingPassPriorityFlow() != flow) {
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

    private void failFlow(BridgePassPriorityFlow flow, RuntimeException e) {
        try {
            flow.fail(e);
        } finally {
            cancelScheduledTickIfCurrent(flow);
            decisionState.clearPendingPassPriorityFlowIfCurrent(flow);
        }
    }

    private void cancelScheduledTickIfInactive(BridgePassPriorityFlow flow) {
        if (flow.isDone() || decisionState.pendingPassPriorityFlow() != flow) {
            cancelScheduledTickIfCurrent(flow);
        }
    }

    private void cancelScheduledTickIfCurrent(BridgePassPriorityFlow flow) {
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
