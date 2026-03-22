package mage.client.bridge.processor;

import mage.game.BridgeLogEntry;
import mage.remote.Session;
import org.apache.log4j.Logger;

import java.util.List;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.function.Supplier;

public final class BridgeGameLogRefresher {
    // TODO(shim): expires=2026-06-30 Delete this server bridge-event sync once
    // the processor can append authoritative game-log records directly from
    // processor-owned event data instead of polling Session.getBridgeEvents().
    private record FetchRequest(long generation, UUID gameId, UUID playerId, int serverCursor) {
    }

    private final BridgeProcessor processor;
    private final BridgeGameState gameState;
    private final BridgeGameLogState gameLogState;
    private final Supplier<Session> sessionSupplier;
    private final Logger logger;
    private final String username;
    private final ExecutorService fetchExecutor;
    private boolean refreshQueued = false;
    private boolean fetchInFlight = false;
    private boolean closed = false;

    public BridgeGameLogRefresher(
            BridgeProcessor processor,
            BridgeGameState gameState,
            BridgeGameLogState gameLogState,
            Supplier<Session> sessionSupplier,
            Logger logger,
            String username) {
        this.processor = processor;
        this.gameState = gameState;
        this.gameLogState = gameLogState;
        this.sessionSupplier = sessionSupplier;
        this.logger = logger;
        this.username = username;
        this.fetchExecutor = Executors.newSingleThreadExecutor(runnable -> {
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
        refreshQueued = true;
        startFetchIfNeeded();
    }

    public void shutdown() {
        closed = true;
        fetchExecutor.shutdownNow();
    }

    private void startFetchIfNeeded() {
        requireProcessorThread("startFetchIfNeeded");
        if (!refreshQueued || fetchInFlight || closed) {
            return;
        }
        Session session = sessionSupplier.get();
        UUID gameId = gameState.currentGameId();
        UUID playerId = gameState.currentPlayerId();
        if (session == null || gameId == null || playerId == null) {
            return;
        }

        FetchRequest request = new FetchRequest(
            gameState.generation(),
            gameId,
            playerId,
            gameLogState.nextServerCursor()
        );
        refreshQueued = false;
        fetchInFlight = true;
        try {
            fetchExecutor.execute(() -> fetchBridgeEvents(request, session));
        } catch (RejectedExecutionException e) {
            fetchInFlight = false;
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
        fetchInFlight = false;
        if (failure != null) {
            logger.error("[" + username + "] Failed to fetch bridge events", failure);
        }
        if (closed) {
            return;
        }
        if (request.generation() == gameState.generation()
                && request.gameId().equals(gameState.currentGameId())
                && request.playerId().equals(gameState.currentPlayerId())) {
            gameLogState.recordFetchedBridgeEvents(fetched);
            if (!fetched.isEmpty()) {
                refreshQueued = true;
            }
        }
        startFetchIfNeeded();
    }

    private void requireProcessorThread(String method) {
        if (!processor.isProcessorThread()) {
            throw new IllegalStateException(method + " must run on the bridge processor thread");
        }
    }
}
