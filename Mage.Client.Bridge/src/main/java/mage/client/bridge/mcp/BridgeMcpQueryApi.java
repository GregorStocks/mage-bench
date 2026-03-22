package mage.client.bridge.mcp;

import mage.cards.decks.DeckCardInfo;
import mage.cards.decks.DeckCardLists;
import mage.client.bridge.processor.BridgeCommand;
import mage.client.bridge.processor.BridgeDecisionState;
import mage.client.bridge.processor.BridgeGameLogState;
import mage.client.bridge.processor.BridgeGameState;
import mage.client.bridge.processor.BridgeProcessor;
import mage.client.bridge.tools.GetGameHistoryTool;
import mage.client.bridge.tools.GetGameLogTool;
import mage.client.bridge.tools.GetGameStateTool;
import mage.client.bridge.tools.GetOracleTextTool;
import mage.client.bridge.tools.McpToolRegistry;
import mage.game.BridgeLogEntry;
import mage.remote.Session;
import mage.view.GameView;
import org.apache.log4j.Logger;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.function.Supplier;
import java.util.function.ToLongFunction;

public final class BridgeMcpQueryApi {
    private final String username;
    private final Logger logger;
    private final BridgeProcessor processor;
    private final BridgeDecisionState decisionState;
    private final BridgeGameState gameState;
    private final BridgeGameLogState gameLogState;
    private final Supplier<Session> sessionSupplier;
    private final Supplier<DeckCardLists> deckListSupplier;
    private final Function<GameView, List<Map<String, Object>>> playersBuilder;
    private final Function<GameView, List<Map<String, Object>>> combatGroupsBuilder;
    private final Function<GameView, List<Map<String, Object>>> stackItemsBuilder;
    private final ToLongFunction<Map<String, Object>> gameStateCursorUpdater;
    private final BridgeOracleTextLookup oracleTextLookup;

    public BridgeMcpQueryApi(
            String username,
            Logger logger,
            BridgeProcessor processor,
            BridgeDecisionState decisionState,
            BridgeGameState gameState,
            BridgeGameLogState gameLogState,
            Supplier<Session> sessionSupplier,
            Supplier<DeckCardLists> deckListSupplier,
            Function<GameView, List<Map<String, Object>>> playersBuilder,
            Function<GameView, List<Map<String, Object>>> combatGroupsBuilder,
            Function<GameView, List<Map<String, Object>>> stackItemsBuilder,
            ToLongFunction<Map<String, Object>> gameStateCursorUpdater,
            BridgeOracleTextLookup oracleTextLookup) {
        this.username = username;
        this.logger = logger;
        this.processor = processor;
        this.decisionState = decisionState;
        this.gameState = gameState;
        this.gameLogState = gameLogState;
        this.sessionSupplier = sessionSupplier;
        this.deckListSupplier = deckListSupplier;
        this.playersBuilder = playersBuilder;
        this.combatGroupsBuilder = combatGroupsBuilder;
        this.stackItemsBuilder = stackItemsBuilder;
        this.gameStateCursorUpdater = gameStateCursorUpdater;
        this.oracleTextLookup = oracleTextLookup;
    }

    public boolean isActionPending() {
        return decisionState.hasPendingAction();
    }

    public GetGameLogTool.Result getGameLogChunk(int maxChars, Integer cursor) {
        BridgeGameLogSnapshot snapshot = snapshotGameLog();
        List<BridgeLogEntry> allEvents = snapshot.events();

        if (cursor != null) {
            final int requestedCursor = cursor;
            List<BridgeLogEntry> responseEvents = allEvents.stream()
                    .filter(e -> e.index() >= requestedCursor)
                    .toList();

            Map<String, Integer> priorTurns = new HashMap<>();
            for (BridgeLogEntry event : allEvents) {
                if (event.index() >= requestedCursor) {
                    break;
                }
                if ("BEGIN_TURN".equals(event.type())) {
                    priorTurns.merge(event.activePlayer(), 1, Integer::sum);
                }
            }

            String rendered = renderGameLogFlat(responseEvents, priorTurns, requestedCursor, false);
            GetGameLogTool.Result result = buildGameLogResult(snapshot, rendered, null, maxChars);

            if (!responseEvents.isEmpty() && responseEvents.get(0).index() > cursor) {
                result.cursor_reset = true;
            }
            return result;
        }

        String rendered = renderGameLogFlat(allEvents, Map.of(), 0, true);
        return buildGameLogResult(snapshot, rendered, rendered.length(), maxChars);
    }

    public GetGameLogTool.Result getGameLogSinceTurn(String player, int sinceTurn) {
        String effectivePlayer = player != null ? player : username;
        BridgeGameLogSnapshot snapshot = snapshotGameLog();
        List<BridgeLogEntry> allEvents = snapshot.events();

        String allRendered = renderGameLogFlat(allEvents, Map.of(), 0, true);

        Map<String, Integer> priorTurns = new HashMap<>();
        int startIdx = -1;
        for (int i = 0; i < allEvents.size(); i++) {
            BridgeLogEntry event = allEvents.get(i);
            if ("BEGIN_TURN".equals(event.type())) {
                int count = priorTurns.merge(event.activePlayer(), 1, Integer::sum);
                if (effectivePlayer.equals(event.activePlayer()) && count == sinceTurn) {
                    priorTurns.merge(effectivePlayer, -1, Integer::sum);
                    startIdx = i;
                    break;
                }
            }
        }

        if (startIdx >= 0) {
            List<BridgeLogEntry> subset = allEvents.subList(startIdx, allEvents.size());
            int minChatCursor = allEvents.get(startIdx).index();
            GetGameLogTool.Result result = buildGameLogResult(
                    snapshot,
                    renderGameLogFlat(subset, priorTurns, minChatCursor, true),
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
        int effectiveCursor = sinceCursor != null ? sinceCursor : 0;
        List<BridgeLogEntry> events = List.of();
        int newCursor = effectiveCursor;
        var gameId = gameState.currentGameId();
        if (gameId != null) {
            Session session = sessionSupplier.get();
            try {
                var playerId = gameState.playerIdForGame(gameId);
                if (playerId != null) {
                    List<BridgeLogEntry> fetched = session.getBridgeEvents(gameId, playerId, effectiveCursor);
                    if (fetched != null && !fetched.isEmpty()) {
                        events = fetched;
                        newCursor = fetched.get(fetched.size() - 1).index() + 1;
                        gameLogState.cacheHistoryEvents(fetched);
                    }
                }
            } catch (Exception e) {
                logger.error("[" + username + "] Failed to fetch bridge events for history", e);
            }
        }

        List<BridgeLogEntry> cachedEvents = gameLogState.snapshotBridgeEvents();
        if (events.isEmpty() && !cachedEvents.isEmpty()) {
            events = sinceCursor != null
                    ? gameLogState.cachedBridgeEventsSince(sinceCursor)
                    : cachedEvents;
            newCursor = nextBridgeEventCursor(cachedEvents);
        }

        if (sinceTurn != null) {
            events = events.stream()
                    .filter(e -> e.turn() >= sinceTurn)
                    .toList();
        }
        return BridgeGameLogFormatter.buildGameHistoryResult(events, newCursor);
    }

    public GetGameStateTool.Result getGameState(Long cursor) {
        return processor.submit(BridgeCommand.of(() -> buildGameStateWithCursor(cursor)));
    }

    public GetGameStateTool.Result getGameState() {
        return processor.submit(BridgeCommand.of(this::buildGameState));
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
        pullBridgeEvents();
        List<BridgeLogEntry> allEvents = gameLogState.snapshotBridgeEvents();
        // TODO(bridge-processor): Replace this MCP-side snapshot/cursor
        // reconstruction with reads from a processor-owned published log that
        // already carries local monotonic sequence numbers.
        return new BridgeGameLogSnapshot(allEvents, nextBridgeEventCursor(allEvents));
    }

    private static int nextBridgeEventCursor(List<BridgeLogEntry> events) {
        return events.isEmpty() ? 0 : events.get(events.size() - 1).index() + 1;
    }

    private GetGameLogTool.Result buildGameLogResult(
            BridgeGameLogSnapshot snapshot,
            String rendered,
            Integer totalLength,
            Integer maxChars) {
        return BridgeGameLogFormatter.buildGameLogResult(snapshot.cursor(), rendered, totalLength, maxChars);
    }

    private List<BridgeLogEntry> pullBridgeEvents() {
        var gameId = gameState.currentGameId();
        if (gameId == null) {
            return List.of();
        }
        var playerId = gameState.playerIdForGame(gameId);
        if (playerId == null) {
            return List.of();
        }
        return gameLogState.pullBridgeEvents(
            sessionSupplier.get(),
            gameId,
            playerId,
            logger,
            username
        );
    }

    private String renderGameLogFlat(
            List<BridgeLogEntry> events,
            Map<String, Integer> initialTurnCounts,
            int minChatCursor,
            boolean includeChat) {
        return BridgeGameLogFormatter.renderGameLogFlat(
            events,
            gameLogState.snapshotChatLog(),
            initialTurnCounts,
            minChatCursor,
            includeChat
        );
    }

    private GetGameStateTool.Result buildGameStateWithCursor(Long cursor) {
        GetGameStateTool.Result fullState = buildGameState();
        if (!Boolean.TRUE.equals(fullState.available)) {
            return fullState;
        }
        long currentCursor = gameStateCursorUpdater.applyAsLong(McpToolRegistry.resultToMap(fullState));
        if (cursor != null && cursor.longValue() == currentCursor) {
            var unchanged = new GetGameStateTool.Result();
            unchanged.available = true;
            unchanged.unchanged = true;
            unchanged.cursor = currentCursor;
            return unchanged;
        }
        fullState.cursor = currentCursor;
        return fullState;
    }

    private GetGameStateTool.Result buildGameState() {
        var state = new GetGameStateTool.Result();
        GameView gameView = gameState.lastGameView();
        if (gameView == null) {
            state.available = false;
            state.error = "No game state available yet";
            return state;
        }

        state.available = true;
        state.game_seq = gameView.getGameSeq();
        String step = gameView.getStep() != null ? gameView.getStep().toString() : "null";
        logger.debug("[" + username + "] getGameState returning game_seq="
                + gameView.getGameSeq() + " step=" + step
                + " thread=" + Thread.currentThread().getName());
        state.turn = gameState.updateRound(gameView);

        if (gameView.getPhase() != null) {
            state.phase = gameView.getPhase().toString();
        }
        if (gameView.getStep() != null) {
            state.step = gameView.getStep().toString();
        }

        state.active_player = gameView.getActivePlayerName();
        state.priority_player = gameView.getPriorityPlayerName();
        state.players = playersBuilder.apply(gameView);
        state.stack = stackItemsBuilder.apply(gameView);

        List<Map<String, Object>> combatGroups = combatGroupsBuilder.apply(gameView);
        if (combatGroups != null) {
            state.combat = combatGroups;
        }

        return state;
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
