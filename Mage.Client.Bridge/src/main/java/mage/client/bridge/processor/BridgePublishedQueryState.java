package mage.client.bridge.processor;

import mage.client.bridge.tools.GetGameStateTool;
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
        BridgePublishedQuerySnapshot previous = publishedSnapshot.get();
        BridgePublishedSnapshotBuild built = buildPublishedSnapshot();
        publishedSnapshot.set(built.snapshot());
        tracePublishedGameStateChange(cause, previous.gameState(), built.gameState(), built.gameStatePayload());
    }

    public void publishProcessorState() {
        publishProcessorState(null);
    }

    public BridgePublishedQuerySnapshot snapshot() {
        return publishedSnapshot.get();
    }

    private BridgePublishedSnapshotBuild buildPublishedSnapshot() {
        // TODO(shim): expires=2026-06-30 Stop rebuilding MCP snapshots from mutable Bridge*State
        // holders once processor-private state and native published read models exist.
        BridgePublishedGameStateBuild gameStateBuild = buildPublishedGameState();
        return new BridgePublishedSnapshotBuild(
            new BridgePublishedQuerySnapshot(
                queryBuilder.buildPublishedActionChoices(),
                gameStateBuild.state(),
                processorState.gameLogState().publishedGameLog(gameLogRefresher.completedSyncEpoch())
            ),
            gameStateBuild.state(),
            gameStateBuild.payload()
        );
    }

    private BridgePublishedGameStateBuild buildPublishedGameState() {
        BridgePublishedQueryBuilder.BridgePublishedGameStateBuild built = queryBuilder.buildPublishedGameState();
        return new BridgePublishedGameStateBuild(
            built.state(),
            built.payload()
        );
    }

    private void tracePublishedGameStateChange(
            BridgeProcessorMessage cause,
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
        logger.info("[" + username + "] published-game-state cause=" + describeCause(cause)
            + " snapshot_id=" + previousSnapshotId + "->" + currentSnapshotId
            + " game_seq=" + (previous != null ? previous.gameSeq() : null) + "->" + current.gameSeq()
            + " payload_prev=" + previousPayload
            + " payload_next=" + currentPayload);
        lastPublishedGameStatePayload = currentPayload;
    }

    private String describeCause(BridgeProcessorMessage cause) {
        if (cause instanceof BridgeCallbackEvent event) {
            return "callback:" + event.method();
        }
        if (cause instanceof BridgeProcessorShutdown shutdown) {
            return "shutdown:" + shutdown.reason();
        }
        if (cause == null) {
            return "unknown";
        }
        return "command:" + cause.getClass().getSimpleName();
    }

    private record BridgePublishedSnapshotBuild(
            BridgePublishedQuerySnapshot snapshot,
            BridgePublishedGameState gameState,
            String gameStatePayload
    ) {
    }

    private record BridgePublishedGameStateBuild(
            BridgePublishedGameState state,
            String payload
    ) {
    }

}
