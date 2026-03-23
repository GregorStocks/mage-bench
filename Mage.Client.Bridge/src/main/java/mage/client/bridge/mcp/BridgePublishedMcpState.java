package mage.client.bridge.mcp;

import mage.client.bridge.processor.BridgeCallbackEvent;
import mage.client.bridge.processor.BridgeGameLogRefresher;
import mage.client.bridge.processor.BridgeProcessorMessage;
import mage.client.bridge.processor.BridgeProcessor;
import mage.client.bridge.processor.BridgeProcessorState;
import mage.client.bridge.processor.BridgeProcessorShutdown;
import mage.client.bridge.tools.GetGameStateTool;
import mage.client.bridge.tools.McpToolRegistry;
import mage.view.GameView;
import org.apache.log4j.Logger;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Function;
import java.util.function.Supplier;
import java.util.function.ToLongFunction;

public final class BridgePublishedMcpState {
    private final Logger logger;
    private final String username;
    private final BridgeProcessor processor;
    private final BridgeProcessorState processorState;
    private final BridgeGameLogRefresher gameLogRefresher;
    private final Supplier<BridgePublishedActionChoices> publishedActionChoicesBuilder;
    private final Function<GameView, List<Map<String, Object>>> playersBuilder;
    private final Function<GameView, List<Map<String, Object>>> combatGroupsBuilder;
    private final Function<GameView, List<Map<String, Object>>> stackItemsBuilder;
    private final ToLongFunction<Map<String, Object>> gameStateSnapshotIdUpdater;
    private final boolean tracePublishedState = Boolean.getBoolean("xmage.bridge.tracePublishedState");
    private final AtomicReference<BridgePublishedMcpSnapshot> publishedSnapshot =
        new AtomicReference<>(BridgePublishedMcpSnapshot.empty());
    private String lastPublishedGameStatePayload = null;

    public BridgePublishedMcpState(
            Logger logger,
            String username,
            BridgeProcessor processor,
            BridgeProcessorState processorState,
            BridgeGameLogRefresher gameLogRefresher,
            Supplier<BridgePublishedActionChoices> publishedActionChoicesBuilder,
            Function<GameView, List<Map<String, Object>>> playersBuilder,
            Function<GameView, List<Map<String, Object>>> combatGroupsBuilder,
            Function<GameView, List<Map<String, Object>>> stackItemsBuilder,
            ToLongFunction<Map<String, Object>> gameStateSnapshotIdUpdater) {
        this.logger = logger;
        this.username = username;
        this.processor = processor;
        this.processorState = processorState;
        this.gameLogRefresher = gameLogRefresher;
        this.publishedActionChoicesBuilder = publishedActionChoicesBuilder;
        this.playersBuilder = playersBuilder;
        this.combatGroupsBuilder = combatGroupsBuilder;
        this.stackItemsBuilder = stackItemsBuilder;
        this.gameStateSnapshotIdUpdater = gameStateSnapshotIdUpdater;
    }

    public void publishProcessorState(BridgeProcessorMessage cause) {
        if (!processor.isProcessorThread()) {
            throw new IllegalStateException("publishProcessorState must run on the bridge processor thread");
        }
        BridgePublishedMcpSnapshot previous = publishedSnapshot.get();
        BridgePublishedSnapshotBuild built = buildPublishedSnapshot();
        publishedSnapshot.set(built.snapshot());
        tracePublishedGameStateChange(cause, previous.gameState(), built.gameState(), built.gameStatePayload());
    }

    public void publishProcessorState() {
        publishProcessorState(null);
    }

    BridgePublishedMcpSnapshot snapshot() {
        return publishedSnapshot.get();
    }

    private BridgePublishedSnapshotBuild buildPublishedSnapshot() {
        // TODO(shim): expires=2026-06-30 Stop rebuilding MCP snapshots from mutable Bridge*State
        // holders once processor-private state and native published read models exist.
        BridgePublishedGameStateBuild gameStateBuild = buildPublishedGameState();
        return new BridgePublishedSnapshotBuild(
            new BridgePublishedMcpSnapshot(
                publishedActionChoicesBuilder.get(),
                gameStateBuild.state(),
                processorState.gameLogState().publishedGameLog(gameLogRefresher.completedSyncEpoch())
            ),
            gameStateBuild.state(),
            gameStateBuild.payload()
        );
    }

    private BridgePublishedGameStateBuild buildPublishedGameState() {
        GameView gameView = processorState.gameState().lastGameView();
        if (gameView == null) {
            return new BridgePublishedGameStateBuild(
                BridgePublishedGameState.unavailable("No game state available yet"),
                "unavailable:error=No game state available yet"
            );
        }

        List<Map<String, Object>> players = freezeMapList(playersBuilder.apply(gameView));
        List<Map<String, Object>> stack = freezeMapList(stackItemsBuilder.apply(gameView));
        List<Map<String, Object>> combat = freezeMapList(combatGroupsBuilder.apply(gameView));

        var state = new GetGameStateTool.Result();
        state.available = true;
        state.game_seq = gameView.getGameSeq();
        state.turn = processorState.gameState().currentRound();
        state.phase = gameView.getPhase() != null ? gameView.getPhase().toString() : null;
        state.step = gameView.getStep() != null ? gameView.getStep().toString() : null;
        state.active_player = gameView.getActivePlayerName();
        state.priority_player = gameView.getPriorityPlayerName();
        state.players = players;
        state.stack = stack;
        state.combat = combat;

        Map<String, Object> stateMap = McpToolRegistry.resultToMap(state);
        long snapshotId = gameStateSnapshotIdUpdater.applyAsLong(stateMap);

        return new BridgePublishedGameStateBuild(
            new BridgePublishedGameState(
                true,
                null,
                snapshotId,
                state.turn,
                state.phase,
                state.step,
                state.active_player,
                state.priority_player,
                players,
                stack,
                combat,
                state.game_seq
            ),
            stateMap.toString()
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
            BridgePublishedMcpSnapshot snapshot,
            BridgePublishedGameState gameState,
            String gameStatePayload
    ) {
    }

    private record BridgePublishedGameStateBuild(
            BridgePublishedGameState state,
            String payload
    ) {
    }

    private static List<Map<String, Object>> freezeMapList(List<Map<String, Object>> values) {
        if (values == null) {
            return null;
        }
        var frozen = new ArrayList<Map<String, Object>>(values.size());
        for (Map<String, Object> value : values) {
            frozen.add(freezeMap(value));
        }
        return Collections.unmodifiableList(frozen);
    }

    private static Map<String, Object> freezeMap(Map<String, Object> value) {
        var frozen = new LinkedHashMap<String, Object>();
        for (Map.Entry<String, Object> entry : value.entrySet()) {
            frozen.put(entry.getKey(), freezeJsonLike(entry.getValue()));
        }
        return Collections.unmodifiableMap(frozen);
    }

    private static Object freezeJsonLike(Object value) {
        if (value instanceof Map<?, ?> map) {
            var frozen = new LinkedHashMap<String, Object>();
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                frozen.put((String) entry.getKey(), freezeJsonLike(entry.getValue()));
            }
            return Collections.unmodifiableMap(frozen);
        }
        if (value instanceof List<?> list) {
            var frozen = new ArrayList<>(list.size());
            for (Object entry : list) {
                frozen.add(freezeJsonLike(entry));
            }
            return Collections.unmodifiableList(frozen);
        }
        return value;
    }
}
