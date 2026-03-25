package mage.client.bridge.processor;

import mage.client.bridge.PendingAction;
import mage.client.bridge.tools.ActionResult;
import mage.client.bridge.tools.McpToolRegistry;
import mage.view.GameView;
import org.apache.log4j.Logger;

import java.util.Map;
import java.util.function.Supplier;
import java.util.function.ToLongFunction;

public final class BridgePublishedQueryState {
    private final Logger logger;
    private final String username;
    private final BridgeProcessor processor;
    private final BridgePublishedQueryBuilder queryBuilder;
    private final Supplier<PendingAction> pendingActionSupplier;
    private final Supplier<BridgeProjectionInputs> projectionInputsSupplier;
    private final Supplier<BridgePublishedGameLog> publishedGameLogSupplier;
    private final ToLongFunction<Map<String, Object>> boardCursorAllocator;
    private final boolean tracePublishedState = Boolean.getBoolean("xmage.bridge.tracePublishedState");
    private final boolean tracePublishedActionChoices = Boolean.getBoolean("xmage.bridge.tracePublishedActionChoices");
    private volatile BridgePublishedQuerySnapshot publishedSnapshot = BridgePublishedQuerySnapshot.empty();
    private BridgePublishedActionChoices projectedActionChoices = BridgePublishedActionChoices.empty();
    private BridgePublishedGameState projectedGameState =
        BridgePublishedGameState.unavailable("No game state available yet");
    private BridgeProjectedActionContext projectedActionContext = BridgeProjectedActionContext.empty();
    private GameView projectedGameView = null;
    private String lastPublishedGameStatePayload = null;

    public BridgePublishedQueryState(
            Logger logger,
            String username,
            BridgeProcessor processor,
            BridgePublishedQueryBuilder queryBuilder,
            Supplier<PendingAction> pendingActionSupplier,
            Supplier<BridgeProjectionInputs> projectionInputsSupplier,
            Supplier<BridgePublishedGameLog> publishedGameLogSupplier,
            ToLongFunction<Map<String, Object>> boardCursorAllocator) {
        this.logger = logger;
        this.username = username;
        this.processor = processor;
        this.queryBuilder = queryBuilder;
        this.pendingActionSupplier = pendingActionSupplier;
        this.projectionInputsSupplier = projectionInputsSupplier;
        this.publishedGameLogSupplier = publishedGameLogSupplier;
        this.boardCursorAllocator = boardCursorAllocator;
    }

    public void publishProcessorState(BridgeProcessorMessage cause) {
        if (!processor.isProcessorThread()) {
            throw new IllegalStateException("publishProcessorState must run on the bridge processor thread");
        }
        publishedSnapshot = buildPublishedSnapshot();
    }

    public void publishProcessorState() {
        publishProcessorState(null);
    }

    public BridgePublishedQuerySnapshot snapshot() {
        return publishedSnapshot;
    }

    public ActionResult currentActionChoicesForRead(Long boardCursorParam) {
        if (!processor.isProcessorThread()) {
            throw new IllegalStateException("currentActionChoicesForRead must run on the bridge processor thread");
        }
        return projectedActionChoices.copyForRead(boardCursorParam);
    }

    public BridgePublishedActionChoices currentProjectedActionChoices() {
        if (!processor.isProcessorThread()) {
            throw new IllegalStateException("currentProjectedActionChoices must run on the bridge processor thread");
        }
        return projectedActionChoices;
    }

    public void projectGameState(GameView gameView, int round, String cause) {
        if (!processor.isProcessorThread()) {
            throw new IllegalStateException("projectGameState must run on the bridge processor thread");
        }
        BridgeProjectionInputs projectionInputs = projectionInputs();
        BridgePublishedQueryBuilder.BridgePublishedGameStateBuild built =
            queryBuilder.buildPublishedGameState(gameView, round, projectionInputs.currentPlayerId());
        BridgePublishedGameState previous = projectedGameState;
        projectedGameState = built.state();
        projectedActionContext = queryBuilder.buildProjectedActionContext(gameView, built.state(), round);
        projectedGameView = gameView;
        projectActionChoices(cause, projectionInputs);
        traceProjectedGameStateChange(cause, previous, built.state(), built.payload());
    }

    public void clearProjectedGameState(String error, String cause) {
        if (!processor.isProcessorThread()) {
            throw new IllegalStateException("clearProjectedGameState must run on the bridge processor thread");
        }
        BridgePublishedGameState next = BridgePublishedGameState.unavailable(error);
        BridgePublishedGameState previous = projectedGameState;
        projectedGameState = next;
        projectedActionContext = BridgeProjectedActionContext.empty();
        projectedGameView = null;
        traceProjectedGameStateChange(
            cause,
            previous,
            next,
            "unavailable:error=" + error
        );
    }

    public void projectActionChoices(String cause) {
        projectActionChoices(cause, projectionInputs());
    }

    private void projectActionChoices(String cause, BridgeProjectionInputs projectionInputs) {
        if (!processor.isProcessorThread()) {
            throw new IllegalStateException("projectActionChoices must run on the bridge processor thread");
        }
        BridgeBuiltActionChoices built = queryBuilder.buildPublishedActionChoices(
            pendingActionSupplier.get(),
            projectedActionContext,
            projectedGameView,
            projectionInputs
        );
        ActionResult result = built.result();
        if (Boolean.TRUE.equals(result.action_pending)) {
            result.board_cursor = boardCursorAllocator.applyAsLong(McpToolRegistry.resultToMap(result));
        }
        BridgePublishedActionChoices previous = projectedActionChoices;
        projectedActionChoices = BridgePublishedActionChoices.from(result, built.backingChoices());
        traceProjectedActionChoicesChange(cause, previous, result);
    }

    public void clearProjectedActionChoices(String cause) {
        if (!processor.isProcessorThread()) {
            throw new IllegalStateException("clearProjectedActionChoices must run on the bridge processor thread");
        }
        projectedActionChoices = BridgePublishedActionChoices.empty();
    }

    private BridgePublishedQuerySnapshot buildPublishedSnapshot() {
        return new BridgePublishedQuerySnapshot(
            projectedActionChoices,
            projectedGameState,
            publishedGameLogSupplier.get()
        );
    }

    private BridgeProjectionInputs projectionInputs() {
        return projectionInputsSupplier.get();
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
