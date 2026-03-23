package mage.client.bridge.mcp;

import mage.cards.decks.DeckCardInfo;
import mage.cards.decks.DeckCardLists;
import mage.client.bridge.processor.BridgeCommand;
import mage.client.bridge.processor.BridgeGameLogRefresher;
import mage.client.bridge.processor.BridgeGameLogState;
import mage.client.bridge.processor.BridgePublishedLogEntry;
import mage.client.bridge.processor.BridgeGameState;
import mage.client.bridge.processor.BridgeProcessor;
import mage.client.bridge.tools.ActionResult;
import mage.client.bridge.tools.GetGameHistoryTool;
import mage.client.bridge.tools.GetGameLogTool;
import mage.client.bridge.tools.GetGameStateTool;
import mage.client.bridge.tools.GetOracleTextTool;
import mage.client.bridge.tools.McpToolRegistry;
import mage.game.BridgeLogEntry;
import mage.view.GameView;
import org.apache.log4j.Logger;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Function;
import java.util.function.Supplier;
import java.util.function.ToLongFunction;

public final class BridgeMcpQueryApi {
    private final String username;
    private final Logger logger;
    private final BridgeProcessor processor;
    private final BridgeGameState gameState;
    private final BridgeGameLogState gameLogState;
    private final BridgeGameLogRefresher gameLogRefresher;
    private final Supplier<BridgePublishedActionChoices> publishedActionChoicesBuilder;
    private final Supplier<DeckCardLists> deckListSupplier;
    private final Function<GameView, List<Map<String, Object>>> playersBuilder;
    private final Function<GameView, List<Map<String, Object>>> combatGroupsBuilder;
    private final Function<GameView, List<Map<String, Object>>> stackItemsBuilder;
    private final ToLongFunction<Map<String, Object>> gameStateCursorUpdater;
    private final BridgeOracleTextLookup oracleTextLookup;
    private final AtomicReference<BridgePublishedMcpSnapshot> publishedSnapshot =
        new AtomicReference<>(BridgePublishedMcpSnapshot.empty());

    public BridgeMcpQueryApi(
            String username,
            Logger logger,
            BridgeProcessor processor,
            BridgeGameState gameState,
            BridgeGameLogState gameLogState,
            BridgeGameLogRefresher gameLogRefresher,
            Supplier<BridgePublishedActionChoices> publishedActionChoicesBuilder,
            Supplier<DeckCardLists> deckListSupplier,
            Function<GameView, List<Map<String, Object>>> playersBuilder,
            Function<GameView, List<Map<String, Object>>> combatGroupsBuilder,
            Function<GameView, List<Map<String, Object>>> stackItemsBuilder,
            ToLongFunction<Map<String, Object>> gameStateCursorUpdater,
            BridgeOracleTextLookup oracleTextLookup) {
        this.username = username;
        this.logger = logger;
        this.processor = processor;
        this.gameState = gameState;
        this.gameLogState = gameLogState;
        this.gameLogRefresher = gameLogRefresher;
        this.publishedActionChoicesBuilder = publishedActionChoicesBuilder;
        this.deckListSupplier = deckListSupplier;
        this.playersBuilder = playersBuilder;
        this.combatGroupsBuilder = combatGroupsBuilder;
        this.stackItemsBuilder = stackItemsBuilder;
        this.gameStateCursorUpdater = gameStateCursorUpdater;
        this.oracleTextLookup = oracleTextLookup;
    }

    public void publishProcessorState() {
        if (!processor.isProcessorThread()) {
            throw new IllegalStateException("publishProcessorState must run on the bridge processor thread");
        }
        publishedSnapshot.set(buildPublishedSnapshot());
    }

    public boolean isActionPending() {
        syncPublishedState();
        return publishedSnapshot.get().actionChoices().actionPending();
    }

    public ActionResult getActionChoices(Long boardCursorParam) {
        syncPublishedState();
        return publishedSnapshot.get().actionChoices().copyForRead(boardCursorParam);
    }

    public ActionResult getActionChoicesSafe(Long boardCursorParam) {
        return getActionChoices(boardCursorParam);
    }

    public GetGameLogTool.Result getGameLogChunk(int maxChars, Integer cursor) {
        BridgeGameLogSnapshot snapshot = snapshotGameLog();
        List<BridgePublishedLogEntry> allEntries = snapshot.entries();

        if (cursor != null) {
            int effectiveCursor = normalizeSnapshotCursor(snapshot, cursor);
            List<BridgePublishedLogEntry> responseEntries = allEntries.stream()
                .filter(entry -> entry.seq() >= effectiveCursor)
                .toList();

            Map<String, Integer> priorTurns = new HashMap<>();
            for (BridgePublishedLogEntry entry : allEntries) {
                if (entry.seq() >= effectiveCursor) {
                    break;
                }
                if (!entry.isBridgeEvent()) {
                    continue;
                }
                BridgeLogEntry event = entry.bridgeEvent();
                if ("BEGIN_TURN".equals(event.type())) {
                    priorTurns.merge(event.activePlayer(), 1, Integer::sum);
                }
            }

            String rendered = renderGameLogFlat(responseEntries, priorTurns);
            GetGameLogTool.Result result = buildGameLogResult(snapshot, rendered, null, maxChars);
            if (effectiveCursor != cursor) {
                result.cursor_reset = true;
            }
            return result;
        }

        String rendered = renderGameLogFlat(allEntries, Map.of());
        return buildGameLogResult(snapshot, rendered, rendered.length(), maxChars);
    }

    public GetGameLogTool.Result getGameLogSinceTurn(String player, int sinceTurn) {
        String effectivePlayer = player != null ? player : username;
        BridgeGameLogSnapshot snapshot = snapshotGameLog();
        List<BridgePublishedLogEntry> allEntries = snapshot.entries();

        String allRendered = renderGameLogFlat(allEntries, Map.of());

        Map<String, Integer> priorTurns = new HashMap<>();
        int startCursor = -1;
        for (BridgePublishedLogEntry entry : allEntries) {
            if (!entry.isBridgeEvent()) {
                continue;
            }
            BridgeLogEntry event = entry.bridgeEvent();
            if ("BEGIN_TURN".equals(event.type())) {
                int count = priorTurns.merge(event.activePlayer(), 1, Integer::sum);
                if (effectivePlayer.equals(event.activePlayer()) && count == sinceTurn) {
                    priorTurns.merge(effectivePlayer, -1, Integer::sum);
                    startCursor = entry.seq();
                    break;
                }
            }
        }

        if (startCursor >= 0) {
            int effectiveCursor = startCursor;
            List<BridgePublishedLogEntry> subset = allEntries.stream()
                .filter(entry -> entry.seq() >= effectiveCursor)
                .toList();
            GetGameLogTool.Result result = buildGameLogResult(
                    snapshot,
                    renderGameLogFlat(subset, priorTurns),
                    allRendered.length(),
                    null
            );
            result.truncated = false;
            result.since_turn = sinceTurn;
            result.since_player = effectivePlayer;
            return result;
        }

        int totalPlayerTurns = priorTurns.getOrDefault(effectivePlayer, 0);
        if (totalPlayerTurns > 0 && sinceTurn <= totalPlayerTurns) {
            GetGameLogTool.Result result = buildGameLogResult(snapshot, allRendered, allRendered.length(), null);
            result.truncated = true;
            result.since_player = effectivePlayer;
            return result;
        }

        GetGameLogTool.Result result = buildGameLogResult(snapshot, "", allRendered.length(), null);
        result.truncated = false;
        return result;
    }

    public GetGameHistoryTool.Result getGameHistory(Integer sinceTurn, Integer sinceCursor) {
        BridgeGameLogSnapshot snapshot = snapshotGameLog();
        int effectiveCursor = sinceCursor != null
            ? normalizeHistoryCursor(snapshot, sinceCursor)
            : snapshot.firstCursor();
        List<BridgeLogEntry> events = snapshot.entries().stream()
            .filter(BridgePublishedLogEntry::isBridgeEvent)
            .filter(entry -> entry.seq() >= effectiveCursor)
            .map(BridgePublishedLogEntry::bridgeEvent)
            .toList();

        if (sinceTurn != null) {
            events = events.stream()
                    .filter(e -> e.turn() >= sinceTurn)
                    .toList();
        }
        return BridgeGameLogFormatter.buildGameHistoryResult(events, snapshot.nextCursor());
    }

    public GetGameStateTool.Result getGameState(Long cursor) {
        return buildGameStateFromPublished(cursor, snapshotGameStateForRead());
    }

    public GetGameStateTool.Result getGameState() {
        return getGameState(null);
    }

    public Map<String, Object> getMyDecklist() {
        var result = new HashMap<String, Object>();
        DeckCardLists deck = deckListSupplier.get();
        if (deck == null) {
            result.put("error", "No deck loaded");
            return result;
        }

        result.put("cards", renderDeckSection(deck.getCards()));
        if (!deck.getSideboard().isEmpty()) {
            result.put("sideboard", renderDeckSection(deck.getSideboard()));
        }
        return result;
    }

    public GetOracleTextTool.Result getOracleText(
            String cardName,
            String objectId,
            String[] cardNames,
            String[] objectIds) {
        return oracleTextLookup.getOracleText(cardName, objectId, cardNames, objectIds);
    }

    private BridgeGameLogSnapshot snapshotGameLog() {
        long syncEpoch = processor.submit(BridgeCommand.of(gameLogRefresher::captureSyncBarrierEpoch));
        gameLogRefresher.awaitSyncThrough(syncEpoch);
        var gameLog = processor.submit(BridgeCommand.of(() -> publishedSnapshot.get().gameLog()));
        return new BridgeGameLogSnapshot(gameLog.entries(), gameLog.firstCursor(), gameLog.nextCursor());
    }

    private BridgePublishedGameState snapshotGameStateForRead() {
        return processor.submit(BridgeCommand.of(() -> {
            BridgePublishedGameState snapshot = publishedSnapshot.get().gameState();
            if (!snapshot.available()) {
                return snapshot;
            }
            var state = new GetGameStateTool.Result();
            state.available = true;
            state.game_seq = snapshot.gameSeq();
            state.turn = snapshot.turn();
            state.phase = snapshot.phase();
            state.step = snapshot.step();
            state.active_player = snapshot.activePlayer();
            state.priority_player = snapshot.priorityPlayer();
            state.players = snapshot.players();
            state.stack = snapshot.stack();
            state.combat = snapshot.combat();
            long currentCursor = gameStateCursorUpdater.applyAsLong(McpToolRegistry.resultToMap(state));
            return new BridgePublishedGameState(
                true,
                null,
                currentCursor,
                snapshot.turn(),
                snapshot.phase(),
                snapshot.step(),
                snapshot.activePlayer(),
                snapshot.priorityPlayer(),
                snapshot.players(),
                snapshot.stack(),
                snapshot.combat(),
                snapshot.gameSeq()
            );
        }));
    }

    private GetGameLogTool.Result buildGameLogResult(
            BridgeGameLogSnapshot snapshot,
            String rendered,
            Integer totalLength,
            Integer maxChars) {
        return BridgeGameLogFormatter.buildGameLogResult(snapshot.nextCursor(), rendered, totalLength, maxChars);
    }

    private String renderGameLogFlat(
            List<BridgePublishedLogEntry> entries,
            Map<String, Integer> initialTurnCounts) {
        return BridgeGameLogFormatter.renderGameLogFlat(entries, initialTurnCounts);
    }

    private GetGameStateTool.Result buildGameStateFromPublished(Long cursor, BridgePublishedGameState snapshot) {
        if (!snapshot.available()) {
            var unavailable = new GetGameStateTool.Result();
            unavailable.available = false;
            unavailable.error = snapshot.error();
            return unavailable;
        }
        if (cursor != null && cursor.longValue() == snapshot.cursor().longValue()) {
            var unchanged = new GetGameStateTool.Result();
            unchanged.available = true;
            unchanged.unchanged = true;
            unchanged.cursor = snapshot.cursor();
            return unchanged;
        }
        var state = new GetGameStateTool.Result();
        state.available = true;
        state.cursor = snapshot.cursor();
        state.game_seq = snapshot.gameSeq();
        String step = snapshot.step() != null ? snapshot.step() : "null";
        logger.debug("[" + username + "] getGameState returning game_seq="
                + snapshot.gameSeq() + " step=" + step
                + " thread=" + Thread.currentThread().getName());
        state.turn = snapshot.turn();
        state.phase = snapshot.phase();
        state.step = snapshot.step();
        state.active_player = snapshot.activePlayer();
        state.priority_player = snapshot.priorityPlayer();
        state.players = snapshot.players();
        state.stack = snapshot.stack();
        state.combat = snapshot.combat();
        return state;
    }

    private BridgePublishedMcpSnapshot buildPublishedSnapshot() {
        // TODO(shim): expires=2026-06-30 Stop rebuilding MCP snapshots from mutable Bridge*State
        // holders once processor-private state and native published read models exist.
        return new BridgePublishedMcpSnapshot(
            publishedActionChoicesBuilder.get(),
            buildPublishedGameState(),
            gameLogState.publishedGameLog()
        );
    }

    private BridgePublishedGameState buildPublishedGameState() {
        GameView gameView = gameState.lastGameView();
        if (gameView == null) {
            return BridgePublishedGameState.unavailable("No game state available yet");
        }

        return new BridgePublishedGameState(
            true,
            null,
            null,
            gameState.currentRound(),
            gameView.getPhase() != null ? gameView.getPhase().toString() : null,
            gameView.getStep() != null ? gameView.getStep().toString() : null,
            gameView.getActivePlayerName(),
            gameView.getPriorityPlayerName(),
            freezeMapList(playersBuilder.apply(gameView)),
            freezeMapList(stackItemsBuilder.apply(gameView)),
            freezeMapList(combatGroupsBuilder.apply(gameView)),
            gameView.getGameSeq()
        );
    }

    private void syncPublishedState() {
        processor.submit(BridgeCommand.of(() -> null));
    }

    private int normalizeSnapshotCursor(BridgeGameLogSnapshot snapshot, int requestedCursor) {
        if (requestedCursor < snapshot.firstCursor() || requestedCursor > snapshot.nextCursor()) {
            return snapshot.firstCursor();
        }
        return requestedCursor;
    }

    private int normalizeHistoryCursor(BridgeGameLogSnapshot snapshot, int requestedCursor) {
        if (requestedCursor < snapshot.firstCursor()) {
            return snapshot.firstCursor();
        }
        if (requestedCursor > snapshot.nextCursor()) {
            return snapshot.nextCursor();
        }
        return requestedCursor;
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

    private static String renderDeckSection(List<DeckCardInfo> cards) {
        var sb = new StringBuilder();
        for (DeckCardInfo card : cards) {
            if (sb.length() > 0) {
                sb.append("\n");
            }
            sb.append(card.getAmount()).append("x ").append(card.getCardName());
        }
        return sb.toString();
    }
}
