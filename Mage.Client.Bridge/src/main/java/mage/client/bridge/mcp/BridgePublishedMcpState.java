package mage.client.bridge.mcp;

import mage.client.bridge.processor.BridgeGameLogState;
import mage.client.bridge.processor.BridgeGameLogRefresher;
import mage.client.bridge.processor.BridgeGameState;
import mage.client.bridge.processor.BridgeProcessor;
import mage.client.bridge.tools.GetGameStateTool;
import mage.client.bridge.tools.McpToolRegistry;
import mage.view.GameView;

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
    private final BridgeProcessor processor;
    private final BridgeGameState gameState;
    private final BridgeGameLogState gameLogState;
    private final BridgeGameLogRefresher gameLogRefresher;
    private final Supplier<BridgePublishedActionChoices> publishedActionChoicesBuilder;
    private final Function<GameView, List<Map<String, Object>>> playersBuilder;
    private final Function<GameView, List<Map<String, Object>>> combatGroupsBuilder;
    private final Function<GameView, List<Map<String, Object>>> stackItemsBuilder;
    private final ToLongFunction<Map<String, Object>> gameStateCursorUpdater;
    private final AtomicReference<BridgePublishedMcpSnapshot> publishedSnapshot =
        new AtomicReference<>(BridgePublishedMcpSnapshot.empty());

    public BridgePublishedMcpState(
            BridgeProcessor processor,
            BridgeGameState gameState,
            BridgeGameLogState gameLogState,
            BridgeGameLogRefresher gameLogRefresher,
            Supplier<BridgePublishedActionChoices> publishedActionChoicesBuilder,
            Function<GameView, List<Map<String, Object>>> playersBuilder,
            Function<GameView, List<Map<String, Object>>> combatGroupsBuilder,
            Function<GameView, List<Map<String, Object>>> stackItemsBuilder,
            ToLongFunction<Map<String, Object>> gameStateCursorUpdater) {
        this.processor = processor;
        this.gameState = gameState;
        this.gameLogState = gameLogState;
        this.gameLogRefresher = gameLogRefresher;
        this.publishedActionChoicesBuilder = publishedActionChoicesBuilder;
        this.playersBuilder = playersBuilder;
        this.combatGroupsBuilder = combatGroupsBuilder;
        this.stackItemsBuilder = stackItemsBuilder;
        this.gameStateCursorUpdater = gameStateCursorUpdater;
    }

    public void publishProcessorState() {
        if (!processor.isProcessorThread()) {
            throw new IllegalStateException("publishProcessorState must run on the bridge processor thread");
        }
        publishedSnapshot.set(buildPublishedSnapshot());
    }

    BridgePublishedMcpSnapshot snapshot() {
        return publishedSnapshot.get();
    }

    private BridgePublishedMcpSnapshot buildPublishedSnapshot() {
        // TODO(shim): expires=2026-06-30 Stop rebuilding MCP snapshots from mutable Bridge*State
        // holders once processor-private state and native published read models exist.
        return new BridgePublishedMcpSnapshot(
            publishedActionChoicesBuilder.get(),
            buildPublishedGameState(),
            gameLogState.publishedGameLog(gameLogRefresher.completedSyncEpoch())
        );
    }

    private BridgePublishedGameState buildPublishedGameState() {
        GameView gameView = gameState.lastGameView();
        if (gameView == null) {
            return BridgePublishedGameState.unavailable("No game state available yet");
        }

        List<Map<String, Object>> players = freezeMapList(playersBuilder.apply(gameView));
        List<Map<String, Object>> stack = freezeMapList(stackItemsBuilder.apply(gameView));
        List<Map<String, Object>> combat = freezeMapList(combatGroupsBuilder.apply(gameView));

        var state = new GetGameStateTool.Result();
        state.available = true;
        state.game_seq = gameView.getGameSeq();
        state.turn = gameState.currentRound();
        state.phase = gameView.getPhase() != null ? gameView.getPhase().toString() : null;
        state.step = gameView.getStep() != null ? gameView.getStep().toString() : null;
        state.active_player = gameView.getActivePlayerName();
        state.priority_player = gameView.getPriorityPlayerName();
        state.players = players;
        state.stack = stack;
        state.combat = combat;

        long cursor = gameStateCursorUpdater.applyAsLong(McpToolRegistry.resultToMap(state));

        return new BridgePublishedGameState(
            true,
            null,
            cursor,
            state.turn,
            state.phase,
            state.step,
            state.active_player,
            state.priority_player,
            players,
            stack,
            combat,
            state.game_seq
        );
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
