package mage.client.bridge.processor;

import mage.constants.PlayerAction;
import mage.remote.Session;
import org.apache.log4j.Logger;

import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;

public final class BridgeConcedeFlowManager {
    private final BridgeProcessor processor;
    private final BridgeGameState gameState;
    private final Supplier<Session> sessionSupplier;
    private final Logger logger;
    private final String username;
    private final long keepAliveConcedeWaitMs;
    private final ScheduledExecutorService scheduler;
    private final Object timeoutLock = new Object();

    private BridgeConcedeFlow pendingFlow = null;
    private ScheduledFuture<?> scheduledTimeout = null;
    private BridgeConcedeFlow scheduledTimeoutFlow = null;

    public BridgeConcedeFlowManager(
            BridgeProcessor processor,
            BridgeGameState gameState,
            Supplier<Session> sessionSupplier,
            Logger logger,
            String username,
            long keepAliveConcedeWaitSeconds) {
        this.processor = processor;
        this.gameState = gameState;
        this.sessionSupplier = sessionSupplier;
        this.logger = logger;
        this.username = username;
        this.keepAliveConcedeWaitMs = keepAliveConcedeWaitSeconds * 1000;
        this.scheduler = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread thread = new Thread(r, "bridge-concede-ticker-" + username);
            thread.setDaemon(true);
            return thread;
        });
    }

    public BridgeConcedeFlow startPendingFlow() {
        if (pendingFlow != null) {
            return pendingFlow;
        }

        UUID gameId = gameState.currentGameId();
        if (gameId == null) {
            logger.warn("[" + username + "] Cannot concede: no active game");
            return BridgeConcedeFlow.completed(null, false);
        }
        if (!gameState.containsActiveGame(gameId)) {
            logger.info("[" + username + "] Game already over, concede is a no-op");
            return BridgeConcedeFlow.completed(gameId, true);
        }

        logger.info("[" + username + "] Conceding game " + gameId);
        sessionSupplier.get().sendPlayerAction(PlayerAction.CONCEDE, gameId, null);

        if (!gameState.keepAliveAfterGame()) {
            return BridgeConcedeFlow.completed(gameId, true);
        }

        BridgeConcedeFlow flow = new BridgeConcedeFlow(gameId);
        pendingFlow = flow;
        if (!gameState.containsActiveGame(gameId)) {
            clearPendingFlowIfCurrent(flow);
            flow.complete(true);
            return flow;
        }

        scheduleTimeout(flow);
        return flow;
    }

    public void advancePendingFlow() {
        BridgeConcedeFlow flow = pendingFlow;
        if (flow == null) {
            return;
        }
        if (!gameState.containsActiveGame(flow.gameId())) {
            finishFlow(flow, true);
        }
    }

    public void shutdown() {
        BridgeConcedeFlow flow = pendingFlow;
        pendingFlow = null;
        cancelScheduledTimeout();
        if (flow != null && !flow.isDone()) {
            flow.complete(false);
        }
        scheduler.shutdownNow();
    }

    private void scheduleTimeout(BridgeConcedeFlow flow) {
        synchronized (timeoutLock) {
            cancelScheduledTimeoutLocked();
            scheduledTimeoutFlow = flow;
            scheduledTimeout = scheduler.schedule(
                () -> timeoutFromScheduler(flow),
                keepAliveConcedeWaitMs,
                TimeUnit.MILLISECONDS
            );
        }
    }

    private void timeoutFromScheduler(BridgeConcedeFlow flow) {
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

    private void timeoutPendingFlow(BridgeConcedeFlow flow) {
        if (pendingFlow != flow) {
            cancelScheduledTimeoutIfCurrent(flow);
            return;
        }
        logger.warn(
            "[" + username + "] Concede sent but GAME_OVER not received within "
                + (keepAliveConcedeWaitMs / 1000) + "s"
        );
        finishFlow(flow, true);
    }

    private void failFlow(BridgeConcedeFlow flow, RuntimeException e) {
        try {
            flow.fail(e);
        } finally {
            clearPendingFlowIfCurrent(flow);
            cancelScheduledTimeoutIfCurrent(flow);
        }
    }

    private void finishFlow(BridgeConcedeFlow flow, boolean value) {
        clearPendingFlowIfCurrent(flow);
        cancelScheduledTimeoutIfCurrent(flow);
        flow.complete(value);
    }

    private void clearPendingFlowIfCurrent(BridgeConcedeFlow flow) {
        if (pendingFlow == flow) {
            pendingFlow = null;
        }
    }

    private void cancelScheduledTimeoutIfCurrent(BridgeConcedeFlow flow) {
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
