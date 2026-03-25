package mage.client.bridge.processor;

import mage.client.bridge.BridgeGameStateBuilder;
import mage.client.bridge.tools.ActionResult;
import mage.client.bridge.tools.McpToolRegistry;
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
    private final boolean tracePublishedActionChoices = Boolean.getBoolean("xmage.bridge.tracePublishedActionChoices");
    private final AtomicReference<BridgePublishedQuerySnapshot> publishedSnapshot =
        new AtomicReference<>(BridgePublishedQuerySnapshot.empty());
    private final AtomicReference<BridgePublishedActionChoices> projectedActionChoices =
        new AtomicReference<>(BridgePublishedActionChoices.empty());
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

    public ActionResult currentActionChoicesForRead(Long boardCursorParam) {
        return projectedActionChoices.get().copyForRead(boardCursorParam);
    }

    public BridgePublishedActionChoices currentProjectedActionChoices() {
        return projectedActionChoices.get();
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
        projectActionChoices(cause);
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

    public void projectActionChoices(String cause) {
        if (!processor.isProcessorThread()) {
            throw new IllegalStateException("projectActionChoices must run on the bridge processor thread");
        }
        BridgeBuiltActionChoices built = queryBuilder.buildPublishedActionChoices(
            processorState.decisionState().pendingAction(),
            projectedGameView,
            projectedRound
        );
        ActionResult result = built.result();
        if (Boolean.TRUE.equals(result.action_pending)) {
            result.board_cursor = processorState.cursorState().updateBoardCursor(
                BridgeGameStateBuilder.buildStateSignature(McpToolRegistry.resultToMap(result))
            );
        }
        BridgePublishedActionChoices previous = projectedActionChoices.getAndSet(
            BridgePublishedActionChoices.from(result, built.backingChoices())
        );
        traceProjectedActionChoicesChange(cause, previous, result);
    }

    public void clearProjectedActionChoices(String cause) {
        if (!processor.isProcessorThread()) {
            throw new IllegalStateException("clearProjectedActionChoices must run on the bridge processor thread");
        }
        projectedActionChoices.set(BridgePublishedActionChoices.empty());
    }

    private BridgePublishedQuerySnapshot buildPublishedSnapshot() {
        return new BridgePublishedQuerySnapshot(
            projectedActionChoices.get(),
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

    private void traceProjectedActionChoicesChange(
            String cause,
            BridgePublishedActionChoices previous,
            ActionResult current) {
        if (!tracePublishedActionChoices) {
            return;
        }
        ActionResult previousResult = previous != null ? previous.copyForRead(null) : null;
        String previousPayload = previousResult != null ? McpToolRegistry.resultToMap(previousResult).toString() : null;
        String currentPayload = McpToolRegistry.resultToMap(current).toString();
        Long previousCursor = previousResult != null ? previousResult.board_cursor : null;
        Long currentCursor = current.board_cursor;
        if (previousPayload != null && previousPayload.equals(currentPayload)) {
            return;
        }
        logger.info("[" + username + "] published-action-choices cause=" + cause
            + " board_cursor=" + previousCursor + "->" + currentCursor
            + " payload_prev=" + previousPayload
            + " payload_next=" + currentPayload);
    }

}
