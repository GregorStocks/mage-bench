package mage.client.bridge.processor;

import mage.game.BridgeLogEntry;
import mage.remote.Session;
import org.apache.log4j.Logger;

import java.util.List;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;

public final class BridgeGameLogRefresher {
    // TODO(shim): expires=2026-06-30 Delete this server bridge-event sync once
    // the processor can append authoritative game-log records directly from
    // processor-owned event data instead of polling Session.getBridgeEvents().
    private record FetchRequest(long requestId, long generation, UUID gameId, UUID playerId, int serverCursor, long syncEpoch) {
    }
    private static final long FETCH_RETRY_DELAY_MS = 250;

    private final BridgeProcessor processor;
    private final BridgeProcessorState processorState;
    private final Supplier<Session> sessionSupplier;
    private final Logger logger;
    private final String username;
    private final ScheduledExecutorService fetchExecutor;
    private final Object syncLock = new Object();
    private boolean refreshQueued = false;
    private boolean closed = false;
    private ScheduledFuture<?> scheduledRetry = null;
    private FetchRequest blockingRequest = null;
    private long nextRequestId = 1;
    private long requestedSyncEpoch = 0;
    private long completedSyncEpoch = 0;

    public BridgeGameLogRefresher(
            BridgeProcessor processor,
            BridgeProcessorState processorState,
            Supplier<Session> sessionSupplier,
            Logger logger,
            String username) {
        this.processor = processor;
        this.processorState = processorState;
        this.sessionSupplier = sessionSupplier;
        this.logger = logger;
        this.username = username;
        this.fetchExecutor = Executors.newScheduledThreadPool(2, runnable -> {
            Thread thread = new Thread(runnable, "bridge-log-refresher-" + username);
            thread.setDaemon(true);
            return thread;
        });
    }

    public void afterCallbackProcessed() {
        requireProcessorThread("afterCallbackProcessed");
        if (closed) {
            return;
        }
        requestedSyncEpoch++;
        refreshQueued = true;
        startFetchIfNeeded();
    }

    public void shutdown() {
        closed = true;
        synchronized (syncLock) {
            completedSyncEpoch = Math.max(completedSyncEpoch, requestedSyncEpoch);
            syncLock.notifyAll();
        }
        if (scheduledRetry != null) {
            scheduledRetry.cancel(false);
            scheduledRetry = null;
        }
        fetchExecutor.shutdownNow();
    }

    public long captureSyncBarrierEpoch() {
        requireProcessorThread("captureSyncBarrierEpoch");
        if (closed
                || sessionSupplier.get() == null
                || processorState.gameState().currentGameId() == null
                || processorState.gameState().currentPlayerId() == null) {
            return completedSyncEpoch;
        }
        return requestedSyncEpoch;
    }

    public long completedSyncEpoch() {
        synchronized (syncLock) {
            return completedSyncEpoch;
        }
    }

    public void awaitSyncThrough(long targetEpoch) {
        synchronized (syncLock) {
            while (!closed && completedSyncEpoch < targetEpoch) {
                try {
                    syncLock.wait();
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    throw new IllegalStateException("Interrupted while waiting for published game-log sync", e);
                }
            }
        }
    }

    private void startFetchIfNeeded() {
        requireProcessorThread("startFetchIfNeeded");
        if (!refreshQueued || closed) {
            return;
        }
        Session session = sessionSupplier.get();
        UUID gameId = processorState.gameState().currentGameId();
        UUID playerId = processorState.gameState().currentPlayerId();
        if (session == null || gameId == null || playerId == null) {
            return;
        }
        if (blockingRequest != null
                && isRequestForCurrentState(blockingRequest, processorState.gameState().generation(), gameId, playerId)) {
            return;
        }
        if (scheduledRetry != null) {
            scheduledRetry.cancel(false);
            scheduledRetry = null;
        }

        FetchRequest request = new FetchRequest(
            nextRequestId++,
            processorState.gameState().generation(),
            gameId,
            playerId,
            processorState.gameLogState().nextServerCursor(),
            requestedSyncEpoch
        );
        refreshQueued = false;
        blockingRequest = request;
        try {
            fetchExecutor.execute(() -> fetchBridgeEvents(request, session));
        } catch (RejectedExecutionException e) {
            blockingRequest = null;
            throw new IllegalStateException("Bridge game log refresher rejected fetch task", e);
        }
    }

    private void fetchBridgeEvents(FetchRequest request, Session session) {
        List<BridgeLogEntry> fetched = null;
        Throwable failure = null;
        try {
            fetched = session.getBridgeEvents(request.gameId(), request.playerId(), request.serverCursor());
        } catch (Throwable t) {
            failure = t;
        }

        List<BridgeLogEntry> finalFetched = fetched != null ? fetched : List.of();
        Throwable finalFailure = failure;
        try {
            processor.submit(BridgeCommand.of(() -> {
                finishFetch(request, finalFetched, finalFailure);
                return null;
            }));
        } catch (IllegalStateException ignored) {
            // Processor is already shut down; nothing left to publish.
        }
    }

    private void finishFetch(FetchRequest request, List<BridgeLogEntry> fetched, Throwable failure) {
        requireProcessorThread("finishFetch");
        if (blockingRequest != null && blockingRequest.requestId() == request.requestId()) {
            blockingRequest = null;
        }
        long completedEpoch = -1;
        boolean currentRequest = request.generation() == processorState.gameState().generation()
                && request.gameId().equals(processorState.gameState().currentGameId())
                && request.playerId().equals(processorState.gameState().currentPlayerId());
        if (failure != null) {
            logger.error("[" + username + "] Failed to fetch bridge events", failure);
        }
        if (closed) {
            return;
        }
        if (currentRequest) {
            processorState.gameLogState().recordFetchedBridgeEvents(fetched);
            if (failure != null) {
                refreshQueued = true;
                completedEpoch = request.syncEpoch();
                scheduleRetry();
            } else if (!fetched.isEmpty()) {
                refreshQueued = true;
            } else if (!refreshQueued) {
                completedEpoch = request.syncEpoch();
            }
        } else {
            completedEpoch = request.syncEpoch();
        }
        if (!currentRequest || failure == null) {
            startFetchIfNeeded();
        }
        if (completedEpoch >= 0) {
            markSyncCompleted(completedEpoch);
        }
    }

    private void scheduleRetry() {
        requireProcessorThread("scheduleRetry");
        if (closed || scheduledRetry != null) {
            return;
        }
        try {
            scheduledRetry = fetchExecutor.schedule(this::retryFetchAfterDelay, FETCH_RETRY_DELAY_MS, TimeUnit.MILLISECONDS);
        } catch (RejectedExecutionException e) {
            throw new IllegalStateException("Bridge game log refresher rejected retry task", e);
        }
    }

    private void retryFetchAfterDelay() {
        try {
            processor.submit(BridgeCommand.of(() -> {
                scheduledRetry = null;
                startFetchIfNeeded();
                return null;
            }));
        } catch (IllegalStateException ignored) {
            // Processor is already shut down; nothing left to publish.
        }
    }

    private void requireProcessorThread(String method) {
        if (!processor.isProcessorThread()) {
            throw new IllegalStateException(method + " must run on the bridge processor thread");
        }
    }

    private static boolean isRequestForCurrentState(FetchRequest request, long generation, UUID gameId, UUID playerId) {
        return request.generation() == generation
                && request.gameId().equals(gameId)
                && request.playerId().equals(playerId);
    }

    private void markSyncCompleted(long syncEpoch) {
        synchronized (syncLock) {
            if (syncEpoch > completedSyncEpoch) {
                completedSyncEpoch = syncEpoch;
            }
            syncLock.notifyAll();
        }
    }
}
