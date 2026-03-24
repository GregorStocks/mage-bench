package mage.client.bridge.processor;

import mage.client.bridge.tools.GetGameStateTool;
import mage.view.GameView;
import org.apache.log4j.Logger;

import java.util.concurrent.atomic.AtomicReference;

public final class BridgePublishedQueryState {
    private final Logger logger;
    private final String username;
    private final BridgeProcessor processor;
    private final BridgeProcessorState processorState;
    private final BridgeGameLogRefresher gameLogRefresher;
    private final BridgePublishedQueryBuilder queryBuilder;
    private final boolean tracePublishedState = Boolean.getBoolean("xmage.bridge.tracePublishedState");
    private final AtomicReference<BridgePublishedQuerySnapshot> publishedSnapshot =
        new AtomicReference<>(BridgePublishedQuerySnapshot.empty());
    private final AtomicReference<BridgePublishedGameState> projectedGameState =
        new AtomicReference<>(BridgePublishedGameState.unavailable("No game state available yet"));
    private volatile GameView projectedGameView = null;
    private volatile int projectedRound = 0;
    private String lastPublishedGameStatePayload = null;

    public BridgePublishedQueryState(
            Logger logger,
            String username,
            BridgeProcessor processor,
            BridgeProcessorState processorState,
            BridgeGameLogRefresher gameLogRefresher,
            BridgePublishedQueryBuilder queryBuilder) {
        this.logger = logger;
        this.username = username;
        this.processor = processor;
        this.processorState = processorState;
        this.gameLogRefresher = gameLogRefresher;
        this.queryBuilder = queryBuilder;
    }

    public void publishProcessorState(BridgeProcessorMessage cause) {
        if (!processor.isProcessorThread()) {
            throw new IllegalStateException("publishProcessorState must run on the bridge processor thread");
        }
        publishedSnapshot.set(buildPublishedSnapshot());
    }

    public void publishProcessorState() {
        publishProcessorState(null);
    }

    public BridgePublishedQuerySnapshot snapshot() {
        return publishedSnapshot.get();
    }

    public void projectGameState(GameView gameView, int round, String cause) {
        if (!processor.isProcessorThread()) {
            throw new IllegalStateException("projectGameState must run on the bridge processor thread");
        }
        BridgePublishedQueryBuilder.BridgePublishedGameStateBuild built =
            queryBuilder.buildPublishedGameState(gameView, round);
        BridgePublishedGameState previous = projectedGameState.getAndSet(built.state());
        projectedGameView = gameView;
        projectedRound = round;
        traceProjectedGameStateChange(cause, previous, built.state(), built.payload());
    }

    public void clearProjectedGameState(String error, String cause) {
        if (!processor.isProcessorThread()) {
            throw new IllegalStateException("clearProjectedGameState must run on the bridge processor thread");
        }
        BridgePublishedGameState next = BridgePublishedGameState.unavailable(error);
        BridgePublishedGameState previous = projectedGameState.getAndSet(next);
        projectedGameView = null;
        projectedRound = 0;
        traceProjectedGameStateChange(
            cause,
            previous,
            next,
            "unavailable:error=" + error
        );
    }

    private BridgePublishedQuerySnapshot buildPublishedSnapshot() {
        // TODO(shim): expires=2026-06-30 Stop rebuilding published action choices from mutable
        // runtime state after the full native query projection lands.
        return new BridgePublishedQuerySnapshot(
            queryBuilder.buildPublishedActionChoices(
                processorState.decisionState().pendingAction(),
                projectedGameView,
                projectedRound
            ),
            projectedGameState.get(),
            processorState.gameLogState().publishedGameLog(gameLogRefresher.completedSyncEpoch())
        );
    }

    private void traceProjectedGameStateChange(
            String cause,
            BridgePublishedGameState previous,
            BridgePublishedGameState current,
            String currentPayload) {
        if (!tracePublishedState || !current.available()) {
            lastPublishedGameStatePayload = currentPayload;
            return;
        }
        Long previousSnapshotId = previous != null ? previous.snapshotId() : null;
        Long currentSnapshotId = current.snapshotId();
        if (previousSnapshotId != null && previousSnapshotId.equals(currentSnapshotId)) {
            lastPublishedGameStatePayload = currentPayload;
            return;
        }
        String previousPayload = lastPublishedGameStatePayload;
        logger.info("[" + username + "] published-game-state cause=" + cause
            + " snapshot_id=" + previousSnapshotId + "->" + currentSnapshotId
            + " game_seq=" + (previous != null ? previous.gameSeq() : null) + "->" + current.gameSeq()
            + " payload_prev=" + previousPayload
            + " payload_next=" + currentPayload);
        lastPublishedGameStatePayload = currentPayload;
    }

}
