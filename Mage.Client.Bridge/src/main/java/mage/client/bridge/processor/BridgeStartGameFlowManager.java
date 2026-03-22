package mage.client.bridge.processor;

import org.apache.log4j.Logger;

import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

public final class BridgeStartGameFlowManager {
    private final BridgeProcessor processor;
    private final Logger logger;
    private final String username;
    private final long startGameWaitMs;
    private final ScheduledExecutorService scheduler;
    private final Object timeoutLock = new Object();

    private BridgeStartGameFlow pendingFlow = null;
    private ScheduledFuture<?> scheduledTimeout = null;
    private BridgeStartGameFlow scheduledTimeoutFlow = null;
    private volatile boolean closed = false;

    public BridgeStartGameFlowManager(
            BridgeProcessor processor,
            Logger logger,
            String username,
            long startGameWaitMs) {
        this.processor = processor;
        this.logger = logger;
        this.username = username;
        this.startGameWaitMs = startGameWaitMs;
        this.scheduler = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread thread = new Thread(r, "bridge-start-game-ticker-" + username);
            thread.setDaemon(true);
            return thread;
        });
    }

    public BridgeStartGameFlow startPendingFlow(UUID expectedTableId) {
        if (closed) {
            throw new IllegalStateException("START_GAME flow manager is shut down");
        }
        if (pendingFlow != null) {
            throw new IllegalStateException("START_GAME flow already pending");
        }
        BridgeStartGameFlow flow = new BridgeStartGameFlow(expectedTableId);
        pendingFlow = flow;
        return flow;
    }

    public void recordJoinedTable(BridgeStartGameFlow flow, UUID tableId) {
        if (pendingFlow != flow || flow.isDone()) {
            return;
        }
        if (closed) {
            finishFlow(flow, false);
            return;
        }
        flow.setExpectedTableId(tableId);
        scheduleTimeout(flow);
    }

    public String ignoreReasonForStartGame(UUID startTableId, boolean keepAliveAfterGame) {
        if (!keepAliveAfterGame) {
            return null;
        }
        BridgeStartGameFlow flow = pendingFlow;
        if (flow == null) {
            return "join_table has not armed a next game";
        }
        UUID expectedTableId = flow.expectedTableId();
        if (expectedTableId != null && !expectedTableId.equals(startTableId)) {
            return "while waiting for table " + expectedTableId;
        }
        return null;
    }

    public void completePendingFlow() {
        BridgeStartGameFlow flow = pendingFlow;
        if (flow == null) {
            return;
        }
        finishFlow(flow, true);
    }

    public void cancelFlow(BridgeStartGameFlow flow) {
        if (flow == null) {
            return;
        }
        finishFlow(flow, false);
    }

    public void shutdown() {
        closed = true;
        BridgeStartGameFlow flow = pendingFlow;
        pendingFlow = null;
        cancelScheduledTimeout();
        if (flow != null && !flow.isDone()) {
            flow.complete(false);
        }
        scheduler.shutdownNow();
    }

    private void scheduleTimeout(BridgeStartGameFlow flow) {
        synchronized (timeoutLock) {
            cancelScheduledTimeoutLocked();
            scheduledTimeoutFlow = flow;
            try {
                scheduledTimeout = scheduler.schedule(
                    () -> timeoutFromScheduler(flow),
                    startGameWaitMs,
                    TimeUnit.MILLISECONDS
                );
            } catch (RejectedExecutionException e) {
                finishFlow(flow, false);
            }
        }
    }

    private void timeoutFromScheduler(BridgeStartGameFlow flow) {
        try {
            processor.submit(BridgeCommand.of(() -> {
                timeoutPendingFlow(flow);
                return null;
            }));
        } catch (IllegalStateException e) {
            if (isProcessorUnavailable(e)) {
                cancelScheduledTimeoutIfCurrent(flow);
                return;
            }
            failFlow(flow, e);
        } catch (RuntimeException e) {
            failFlow(flow, e);
        }
    }

    private void timeoutPendingFlow(BridgeStartGameFlow flow) {
        if (pendingFlow != flow) {
            cancelScheduledTimeoutIfCurrent(flow);
            return;
        }
        logger.warn(
            "[" + username + "] Joined table but START_GAME not received within "
                + (startGameWaitMs / 1000) + "s"
        );
        finishFlow(flow, false);
    }

    private void failFlow(BridgeStartGameFlow flow, RuntimeException e) {
        try {
            flow.fail(e);
        } finally {
            clearPendingFlowIfCurrent(flow);
            cancelScheduledTimeoutIfCurrent(flow);
        }
    }

    private void finishFlow(BridgeStartGameFlow flow, boolean value) {
        clearPendingFlowIfCurrent(flow);
        cancelScheduledTimeoutIfCurrent(flow);
        flow.complete(value);
    }

    private void clearPendingFlowIfCurrent(BridgeStartGameFlow flow) {
        if (pendingFlow == flow) {
            pendingFlow = null;
        }
    }

    private void cancelScheduledTimeoutIfCurrent(BridgeStartGameFlow flow) {
        synchronized (timeoutLock) {
            if (scheduledTimeoutFlow == flow) {
                cancelScheduledTimeoutLocked();
            }
        }
    }

    private void cancelScheduledTimeout() {
        synchronized (timeoutLock) {
            cancelScheduledTimeoutLocked();
        }
    }

    private void cancelScheduledTimeoutLocked() {
        if (scheduledTimeout != null) {
            scheduledTimeout.cancel(false);
            scheduledTimeout = null;
        }
        scheduledTimeoutFlow = null;
    }

    private static boolean isProcessorUnavailable(IllegalStateException e) {
        return "Bridge processor is shut down".equals(e.getMessage())
            || "Interrupted while waiting for bridge processor".equals(e.getMessage());
    }
}
