package mage.client.bridge;

import mage.client.bridge.listener.BridgeCallbackIngress;
import mage.client.bridge.mcp.BridgeMcpActionApi;
import mage.client.bridge.mcp.BridgeMcpQueryApi;
import mage.client.bridge.processor.BridgeActionCommandService;
import mage.client.bridge.processor.BridgeCallbackDispatcher;
import mage.client.bridge.processor.BridgeCallbackEvent;
import mage.client.bridge.processor.BridgeCallbackProcessorService;
import mage.client.bridge.processor.BridgeChooseActionFlow;
import mage.client.bridge.processor.BridgeChooseActionInput;
import mage.client.bridge.processor.BridgeChooseActionFlowManager;
import mage.client.bridge.processor.BridgeChooseActionStartResult;
import mage.client.bridge.processor.BridgeCommand;
import mage.client.bridge.processor.BridgeConcedeFlowManager;
import mage.client.bridge.processor.BridgeDecisionFlowService;
import mage.client.bridge.processor.BridgeGameLogRefresher;
import mage.client.bridge.processor.BridgeManaPlanEntry;
import mage.client.bridge.processor.BridgePassPriorityFlow;
import mage.client.bridge.processor.BridgePassPriorityFlowContextImpl;
import mage.client.bridge.processor.BridgePassPriorityFlowManager;
import mage.client.bridge.processor.BridgeProcessor;
import mage.client.bridge.processor.BridgeProcessorState;
import mage.client.bridge.processor.BridgePublishedActionChoices;
import mage.client.bridge.processor.BridgePublishedQueryBuilder;
import mage.client.bridge.processor.BridgePublishedQueryState;
import mage.client.bridge.processor.BridgeQueryCommandService;
import mage.client.bridge.processor.BridgeStartGameFlow;
import mage.client.bridge.processor.BridgeStartGameFlowManager;
import mage.client.bridge.processor.BridgeChooseActionFlowContextImpl;
import mage.cards.decks.DeckCardInfo;
import mage.cards.decks.DeckCardLists;
import mage.cards.repository.CardInfo;
import mage.cards.repository.CardRepository;
import mage.choices.Choice;
import mage.constants.ManaType;
import mage.constants.PhaseStep;
import mage.constants.SubType;
import mage.constants.SubTypeSet;
import mage.interfaces.callback.ClientCallback;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.remote.Session;
import mage.view.AbilityPickerView;
import mage.view.CardsView;
import mage.view.CardView;
import mage.view.CombatGroupView;
import mage.view.GameClientMessage;
import mage.view.GameView;
import mage.view.ManaPoolView;
import mage.view.PermanentView;
import mage.view.PlayerView;
import mage.players.PlayableObjectsList;
import mage.players.PlayableObjectStats;
import mage.util.MultiAmountMessage;
import mage.util.ShortIdRegistry;

import mage.client.bridge.tools.ActionResult;
import mage.client.bridge.tools.ChooseActionTool;
import mage.client.bridge.tools.GetGameHistoryTool;
import mage.client.bridge.tools.GetGameLogTool;
import mage.client.bridge.tools.GetGameStateTool;
import mage.client.bridge.tools.GetOracleTextTool;

import java.io.FileWriter;
import java.io.IOException;
import java.io.PrintWriter;
import java.io.Serializable;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import org.apache.log4j.Logger;

import java.util.Comparator;
import java.util.Objects;
import java.util.ArrayList;
import java.util.EnumSet;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;
import java.util.function.Supplier;

/**
 * Callback handler for the bridge client.
 * Stores pending actions for external clients to handle via MCP.
 * Higher-level controller roles such as
 * pilot, replay, and the Python-side sleepwalker live above this layer.
 */
public class BridgeCallbackHandler {

    private static final Logger logger = Logger.getLogger(BridgeCallbackHandler.class);
    private static final int SHUTDOWN_FLOW_DRAIN_MAX_PASSES = 8;
    private static final long START_GAME_WAIT_MS = 60_000;

    private final BridgeMageClient client;
    private final BridgeViewLocator viewLocator;
    private final BridgeCardFormatter cardFormatter;
    private final BridgeGameStateBuilder gameStateBuilder;
    private final BridgeOracleTextService oracleTextService;
    private final BridgeChooseActionFlowManager chooseActionFlowManager;
    private final BridgePassPriorityFlowManager passPriorityFlowManager;
    private final BridgeConcedeFlowManager concedeFlowManager;
    private final BridgeStartGameFlowManager startGameFlowManager;
    private final BridgeCallbackIngress callbackIngress;
    private final BridgeMcpActionApi mcpActionApi;
    private final BridgeMcpQueryApi mcpQueryApi;
    private final BridgePublishedQueryBuilder publishedQueryBuilder;
    private final BridgePublishedQueryState publishedQueryState;
    private final BridgeProcessor processor;
    private final BridgeProcessorState processorState = new BridgeProcessorState();
    private final BridgeDecisionFlowService decisionFlowService;
    private final BridgeGameLogRefresher gameLogRefresher;
    private final BridgeActionCommandService actionCommandService;
    private final BridgeQueryCommandService queryCommandService;
    private volatile Session session;
    private final ShortIdRegistry shortIds = new ShortIdRegistry("l");
    private static final long CHAT_DEDUP_WINDOW_MS = 30_000;
    private static final long KEEPALIVE_CONCEDE_WAIT_SECONDS = 15;

    private volatile DeckCardLists deckList = null; // Original decklist for get_my_decklist
    private volatile String errorLogPath = null; // Path to write errors to (set via system property)
    private volatile String bridgeLogPath = null; // Path to write bridge JSONL dump
    // Join handler: provided by BridgeClient so JoinTableTool can trigger table joining
    @FunctionalInterface
    public interface JoinHandler {
        UUID joinTable(String deckPath, UUID targetTableId) throws Exception;
    }
    private volatile JoinHandler joinHandler = null;

    // Track actionable callbacks (GAME_SELECT, GAME_ASK, etc.) separately from passive
    // ones (CHATMESSAGE, GAME_UPDATE). Used by zombie detection and progress logging.
    private static final EnumSet<ClientCallbackMethod> ACTIONABLE_CALLBACKS = EnumSet.of(
        ClientCallbackMethod.GAME_SELECT, ClientCallbackMethod.GAME_ASK,
        ClientCallbackMethod.GAME_TARGET, ClientCallbackMethod.GAME_CHOOSE_ABILITY,
        ClientCallbackMethod.GAME_CHOOSE_CHOICE, ClientCallbackMethod.GAME_CHOOSE_PILE,
        ClientCallbackMethod.GAME_PLAY_MANA, ClientCallbackMethod.GAME_PLAY_XMANA,
        ClientCallbackMethod.GAME_GET_AMOUNT, ClientCallbackMethod.GAME_GET_MULTI_AMOUNT);
    private static final ZoneId LOG_TZ = ZoneId.of("America/Los_Angeles");
    private static final DateTimeFormatter TIME_FMT =
        DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSSSSSXXX");

    public BridgeCallbackHandler(BridgeMageClient client) {
        this.client = client;
        this.viewLocator = new BridgeViewLocator(shortIds, processorState.gameState()::lastGameView, this::logError);
        this.cardFormatter = new BridgeCardFormatter(
            viewLocator,
            processorState.gameState()::currentGameId,
            this::playerIdForGame
        );
        this.gameStateBuilder = new BridgeGameStateBuilder(
            cardFormatter,
            viewLocator,
            processorState.gameState()::currentGameId,
            this::playerIdForGame
        );
        this.oracleTextService = new BridgeOracleTextService(shortIds, viewLocator);
        var processorRef = new AtomicReference<BridgeProcessor>();
        var startGameFlowManagerRef = new AtomicReference<BridgeStartGameFlowManager>();
        var callbackProcessorService = new BridgeCallbackProcessorService(
            client.getUsername(),
            logger,
            processorState,
            processorRef::get,
            startGameFlowManagerRef::get,
            ACTIONABLE_CALLBACKS::contains,
            () -> session,
            () -> bridgeLogPath != null,
            this::logBridgeEvent,
            this::logError,
            this::advancePendingFlows,
            client::stop,
            shortIds::clear,
            CHAT_DEDUP_WINDOW_MS
        );
        BridgeCallbackDispatcher dispatcher = new BridgeCallbackDispatcher(callbackProcessorService);
        this.processor = new BridgeProcessor(client.getUsername(), logger, dispatcher::process);
        processorRef.set(this.processor);
        this.callbackIngress = new BridgeCallbackIngress(
            ACTIONABLE_CALLBACKS::contains,
            processor::enqueueCallback,
            callbackProcessorService
        );
        this.gameLogRefresher = new BridgeGameLogRefresher(
            processor,
            processorState,
            () -> session,
            logger,
            client.getUsername()
        );
        this.publishedQueryBuilder = new BridgePublishedQueryBuilder(
            client.getUsername(),
            processorState,
            gameStateBuilder,
            cardFormatter,
            viewLocator,
            () -> deckList
        );
        this.publishedQueryState = new BridgePublishedQueryState(
            logger,
            client.getUsername(),
            processor,
            processorState,
            gameLogRefresher,
            publishedQueryBuilder
        );
        this.queryCommandService = new BridgeQueryCommandService(
            () -> deckList,
            oracleTextService::getOracleText
        );
        this.mcpQueryApi = new BridgeMcpQueryApi(
            client.getUsername(),
            logger,
            processor,
            gameLogRefresher,
            publishedQueryState,
            queryCommandService,
            this::awaitPublishedReadBarrier,
            this::awaitProcessorReadBarrier
        );
        this.decisionFlowService = new BridgeDecisionFlowService(
            client.getUsername(),
            logger,
            processorState,
            publishedQueryBuilder,
            viewLocator,
            cardFormatter,
            shortIds,
            () -> session,
            client::isRunning,
            this::logError,
            this::logBridgeEvent
        );
        this.chooseActionFlowManager = new BridgeChooseActionFlowManager(
            processor,
            client.getUsername(),
            processorState.decisionState(),
            new BridgeChooseActionFlowContextImpl(decisionFlowService, processorState),
            decisionFlowService::chooseActionDeliveryErrorResult
        );
        this.passPriorityFlowManager = new BridgePassPriorityFlowManager(
            processor,
            client.getUsername(),
            processorState.decisionState(),
            new BridgePassPriorityFlowContextImpl(decisionFlowService, processorState)
        );
        this.concedeFlowManager = new BridgeConcedeFlowManager(
            processor,
            processorState,
            () -> session,
            logger,
            client.getUsername(),
            KEEPALIVE_CONCEDE_WAIT_SECONDS
        );
        this.startGameFlowManager = new BridgeStartGameFlowManager(
            processor,
            logger,
            client.getUsername(),
            START_GAME_WAIT_MS
        );
        startGameFlowManagerRef.set(this.startGameFlowManager);
        this.actionCommandService = new BridgeActionCommandService(
            client.getUsername(),
            logger,
            processorState,
            gameLogRefresher,
            chooseActionFlowManager,
            passPriorityFlowManager,
            concedeFlowManager,
            () -> session,
            CHAT_DEDUP_WINDOW_MS,
            decisionFlowService::executeDefaultAction,
            result -> decisionFlowService.attachUnseenChat(result),
            result -> decisionFlowService.attachUnseenChat(result)
        );
        this.mcpActionApi = new BridgeMcpActionApi(processor, actionCommandService);
        this.processor.setAfterMessageHook(message -> {
            if (message instanceof BridgeCallbackEvent event
                    && processorState.gameState().currentGameId() != null
                    && processorState.gameState().currentGameId().equals(event.objectId())) {
                gameLogRefresher.afterCallbackProcessed();
            }
            publishedQueryState.publishProcessorState(message);
        });
        this.processor.start();
    }

    public void setErrorLogPath(String path) {
        this.errorLogPath = path;
    }

    public void setBridgeLogPath(String path) {
        this.bridgeLogPath = path;
    }

    /**
     * Append an error line to the error log file (if configured).
     * Also logs via log4j as usual.
     */
    void logError(String msg) {
        logger.error(msg);
        String path = errorLogPath;
        if (path != null) {
            try (PrintWriter pw = new PrintWriter(new FileWriter(path, true))) {
                pw.println("[" + ZonedDateTime.now(LOG_TZ).format(TIME_FMT) + "] [mcp] " + msg);
            } catch (IOException e) {
                logger.warn("Failed to write to error log: " + e.getMessage());
            }
        }
    }

    private void logBridgeEvent(String method, UUID gameId, String summary) {
        String path = bridgeLogPath;
        if (path == null) {
            return;
        }
        try (PrintWriter pw = new PrintWriter(new FileWriter(path, true))) {
            var sb = new StringBuilder();
            sb.append("{\"ts\":\"").append(ZonedDateTime.now(LOG_TZ).format(TIME_FMT)).append("\"");
            sb.append(",\"method\":\"").append(method).append("\"");
            if (gameId != null) {
                sb.append(",\"gameId\":\"").append(gameId).append("\"");
            }
            if (summary != null && !summary.isEmpty()) {
                // Escape JSON string
                sb.append(",\"data\":").append(escapeJsonString(summary));
            }
            sb.append("}");
            pw.println(sb.toString());
        } catch (IOException e) {
            logger.debug("Failed to write bridge log: " + e.getMessage());
        }
    }

    /**
     * Escape a string for JSON embedding. Returns a quoted JSON string.
     */
    private static String escapeJsonString(String s) {
        var sb = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"' -> sb.append("\\\"");
                case '\\' -> sb.append("\\\\");
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
                default -> {
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
                }
            }
        }
        sb.append("\"");
        return sb.toString();
    }

    private UUID playerIdForGame(UUID gameId) {
        return processorState.gameState().playerIdForGame(gameId);
    }

    public void setSession(Session session) {
        this.session = session;
    }

    public void setKeepAliveAfterGame(boolean keepAliveAfterGame) {
        processorState.gameState().setKeepAliveAfterGame(keepAliveAfterGame);
        logger.info("[" + client.getUsername() + "] keepAliveAfterGame=" + keepAliveAfterGame);
    }

    public void setDeckList(DeckCardLists deckList) {
        this.deckList = deckList;
    }

    public void setMaxInteractionsPerTurn(int max) {
        int effectiveMax = Math.max(5, max);
        processorState.interactionState().setMaxInteractionsPerTurn(effectiveMax);
        logger.info("[" + client.getUsername() + "] maxInteractionsPerTurn set to " + effectiveMax);
    }

    public void setJoinHandler(JoinHandler handler) {
        this.joinHandler = handler;
    }

    /**
     * Create a fresh handler for the next game, copying only persistent config fields.
     * Installs the new handler on the client (so callbacks route to it) and returns it.
     */
    public BridgeCallbackHandler createFreshForNextGame() {
        // Mark this handler as superseded so threads stuck in
        // awaitPendingAction / passPriority / chooseAction bail out immediately
        // instead of blocking for 120+ seconds on an abandoned handler.
        processorState.gameState().markSuperseded();
        shutdownProcessor("superseded by createFreshForNextGame");

        BridgeCallbackHandler fresh = new BridgeCallbackHandler(client);
        fresh.session = this.session;
        fresh.processorState.gameState().setKeepAliveAfterGame(this.processorState.gameState().keepAliveAfterGame());
        fresh.processorState.interactionState().setMaxInteractionsPerTurn(this.processorState.interactionState().maxInteractionsPerTurn());
        fresh.errorLogPath = this.errorLogPath;
        fresh.bridgeLogPath = this.bridgeLogPath;
        fresh.joinHandler = this.joinHandler;
        client.setCallbackHandler(fresh);
        logger.info("[" + client.getUsername() + "] Created fresh handler for next game");
        return fresh;
    }

    /**
     * Join the next available game table with a new deck. Used by JoinTableTool.
     * Creates a fresh handler (discarding all old game state), loads the deck,
     * joins a table, and waits for game start.
     */
    public void joinNextTable(String deckPath, UUID targetTableId) throws Exception {
        JoinHandler jh = this.joinHandler;
        assert jh != null : "joinHandler not set — keepAlive mode requires a JoinHandler";
        BridgeCallbackHandler fresh = createFreshForNextGame();
        DeckCardLists deck = BridgeClient.loadDeck(deckPath);
        fresh.setDeckList(deck);
        BridgeStartGameFlow flow = fresh.startPendingStartGameFlow(targetTableId);
        try {
            UUID tableId = jh.joinTable(deckPath, targetTableId);
            assert tableId != null : "Failed to join any table within timeout";
            fresh.recordJoinedStartGameTable(flow, tableId);
            logger.info("[" + client.getUsername() + "] Joined table " + tableId + ", waiting for game start...");
            boolean started = flow.awaitResult();
            assert started : "Game did not start within 60s after joining table";
            logger.info("[" + client.getUsername() + "] Game started after join_table");
        } catch (InterruptedException e) {
            fresh.cancelPendingStartGameFlow(flow);
            Thread.currentThread().interrupt();
            throw e;
        } catch (ExecutionException e) {
            fresh.cancelPendingStartGameFlow(flow);
            Throwable cause = e.getCause();
            if (cause instanceof RuntimeException runtimeException) {
                throw runtimeException;
            }
            throw new IllegalStateException("join_table start-game flow failed", cause);
        } catch (Exception | AssertionError e) {
            fresh.cancelPendingStartGameFlow(flow);
            throw e;
        }
    }

    public void reset() {
        processor.submit(new BridgeCommand<Void>() {
            @Override
            public Void execute() {
                resetProcessorState();
                return null;
            }
        });
    }

    private void resetProcessorState() {
        processorState.reset();
    }

    private void awaitPublishedReadBarrier() {
        try {
            client.awaitCallbackListenerIdle();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Interrupted while waiting for bridge callback listener idle", e);
        }
        awaitProcessorReadBarrier();
    }

    private void awaitProcessorReadBarrier() {
        processor.submit(new BridgeCommand<Void>() {
            @Override
            public Void execute() {
                return null;
            }
        });
    }

    // Visible for tests that need to wait for queued callback processing.
    void awaitProcessorIdle() {
        awaitProcessorReadBarrier();
    }

    void shutdownProcessor(String reason) {
        startGameFlowManager.shutdown();
        gameLogRefresher.shutdown();
        advancePendingFlowsBeforeShutdown();
        chooseActionFlowManager.shutdown();
        passPriorityFlowManager.shutdown();
        concedeFlowManager.shutdown();
        processor.shutdown(reason);
    }

    private BridgeStartGameFlow startPendingStartGameFlow(UUID expectedTableId) {
        return processor.submit(BridgeCommand.of(() -> startGameFlowManager.startPendingFlow(expectedTableId)));
    }

    private void recordJoinedStartGameTable(BridgeStartGameFlow flow, UUID tableId) {
        try {
            processor.submit(BridgeCommand.of(() -> {
                startGameFlowManager.recordJoinedTable(flow, tableId);
                return null;
            }));
        } catch (IllegalStateException ignored) {
            startGameFlowManager.cancelFlow(flow);
        }
    }

    private void cancelPendingStartGameFlow(BridgeStartGameFlow flow) {
        try {
            processor.submit(BridgeCommand.of(() -> {
                startGameFlowManager.cancelFlow(flow);
                return null;
            }));
        } catch (IllegalStateException ignored) {
            startGameFlowManager.cancelFlow(flow);
        }
    }

    public boolean isActionPending() {
        return mcpQueryApi.isActionPending();
    }

    public Map<String, Object> executeDefaultAction() {
        return mcpActionApi.executeDefaultAction();
    }

    /**
     * Get structured information about the current pending action's available choices.
     * Returns indexed choices so external clients can pick by index via chooseAction().
     */
    @SuppressWarnings("unchecked")
    public ActionResult getActionChoices(Long boardCursorParam) {
        return mcpQueryApi.getActionChoices(boardCursorParam);
    }

    /**
     * MCP tool boundary wrapper for get_action_choices.
     */
    public ActionResult getActionChoicesSafe(Long boardCursorParam) {
        return mcpQueryApi.getActionChoicesSafe(boardCursorParam);
    }
    static String validateMultiAmountInput(GameClientMessage msg, int[] amounts) {
        return BridgeDecisionFlowService.validateMultiAmountInput(msg, amounts);
    }

    public ChooseActionTool.Result chooseAction(Integer index, String id, Boolean answer, Integer amount, int[] amounts, Integer pile, String text, String[] manaPlanArray, Boolean autoTap, String[] attackers, String[] blockersArray) {
        return mcpActionApi.chooseAction(
            index,
            id,
            answer,
            amount,
            amounts,
            pile,
            text,
            manaPlanArray,
            autoTap,
            attackers,
            blockersArray
        );
    }

    private void advancePendingFlows() {
        chooseActionFlowManager.advancePendingFlow();
        passPriorityFlowManager.advancePendingFlow();
        concedeFlowManager.advancePendingFlow();
    }

    private void advancePendingFlowsBeforeShutdown() {
        try {
            processor.submit(BridgeCommand.of(() -> {
                for (int pass = 0; pass < SHUTDOWN_FLOW_DRAIN_MAX_PASSES; pass++) {
                    BridgeChooseActionFlow chooseActionBefore = processorState.decisionState().pendingChooseActionFlow();
                    BridgePassPriorityFlow passPriorityBefore = processorState.decisionState().pendingPassPriorityFlow();
                    PendingAction pendingActionBefore = processorState.decisionState().pendingAction();
                    advancePendingFlows();
                    if (processorState.decisionState().pendingChooseActionFlow() == null
                            && processorState.decisionState().pendingPassPriorityFlow() == null) {
                        return null;
                    }
                    if (processorState.decisionState().pendingChooseActionFlow() == chooseActionBefore
                            && processorState.decisionState().pendingPassPriorityFlow() == passPriorityBefore
                            && processorState.decisionState().pendingAction() == pendingActionBefore) {
                        return null;
                    }
                }
                return null;
            }));
        } catch (IllegalStateException ignored) {
            // Processor is already gone; pending callers will observe shutdown state.
        }
    }

    public GetGameLogTool.Result getGameLogChunk(int maxChars, Integer cursor) {
        return mcpQueryApi.getGameLogChunk(maxChars, cursor);
    }

    /**
     * Return game log entries starting from a specific player's Nth per-player turn.
     * Computes per-player turn numbers from BEGIN_TURN bridge events at read time.
     * If player is null, defaults to this client's player name.
     */
    public GetGameLogTool.Result getGameLogSinceTurn(String player, int sinceTurn) {
        return mcpQueryApi.getGameLogSinceTurn(player, sinceTurn);
    }

    /**
     * Get structured game history from bridge events.
     * Pulls all events from the server, groups by turn/phase, and formats
     * human-readable descriptions from the structured BridgeLogEntry fields.
     *
     * @param sinceTurn if non-null, only include events from this turn number onward
     * @param sinceCursor if non-null, only include events with index >= this value (incremental)
     * @return map with "history" (formatted text), "cursor" (for next incremental call),
     *         "event_count" (number of events included)
     */
    public GetGameHistoryTool.Result getGameHistory(Integer sinceTurn, Integer sinceCursor) {
        return mcpQueryApi.getGameHistory(sinceTurn, sinceCursor);
    }

    /**
     * Send a chat message. Returns null on success, or an error string on failure.
     */
    public String sendChatMessage(String message) {
        return mcpActionApi.sendChatMessage(message);
    }

    /**
     * Concede the current game and wait for the server to confirm it ended.
     *
     * Blocking until GAME_OVER is critical for multi-game (keepAlive) sessions:
     * the Python test harness starts the next game immediately after concede
     * returns.  If we return before the server processes the game end, the
     * opponent bridge may still be in handleGameOver pulling bridge events
     * and unable to join the next table — causing a bridge_join timeout flake.
     */
    public boolean concede() {
        return mcpActionApi.concede();
    }

    /**
     * Pass priority. Without until: passes once and returns. With until set to a
     * step name (upkeep, draw, etc.): client-side yield that auto-passes until
     * the target step is reached. With until set to a cross-turn value
     * (end_of_turn, my_turn, stack_resolved): client-side yield that auto-passes
     * each callback locally via sendPlayerBoolean(false) until the yield
     * condition is met.
     *
     * All yield modes are client-side to avoid a race condition in XMage's
     * server-side skip() which bypasses waitResponseOpen(), allowing stale
     * responses to answer the wrong waitForResponse().
     *
     * Auto-handles mechanical callbacks (GAME_PLAY_MANA auto-cancel,
     * optional GAME_TARGET with no legal targets). Returns stop_reason indicating
     * why the call returned. When action_pending=true, also includes the full
     * action choices (same data as get_action_choices) so the LLM can respond
     * immediately without a separate round-trip.
     */
    public ActionResult passPriority(String until, Long boardCursorParam) {
        return mcpActionApi.passPriority(until, boardCursorParam);
    }

    /**
     * Combined helper for models: wait using pass_priority, then return full choices.
     * pass_priority already merges action choices, so this is just a pass-through.
     */
    public ActionResult waitAndGetChoices(String until, Long boardCursorParam) {
        return mcpActionApi.waitAndGetChoices(until, boardCursorParam);
    }


    public GetGameStateTool.Result getGameState(Long snapshotId) {
        return mcpQueryApi.getGameState(snapshotId);
    }

    public GetGameStateTool.Result getGameState() {
        return mcpQueryApi.getGameState();
    }

    public Map<String, Object> getMyDecklist() {
        return mcpQueryApi.getMyDecklist();
    }

    public GetOracleTextTool.Result getOracleText(String cardName, String objectId, String[] cardNames, String[] objectIds) {
        return mcpQueryApi.getOracleText(cardName, objectId, cardNames, objectIds);
    }

    public void handleCallback(ClientCallback callback) {
        callbackIngress.handleCallback(callback);
    }

    static String stripAbilityPickerOrdinalPrefix(String description, int zeroBasedIndex) {
        return BridgePromptFormatting.stripAbilityPickerOrdinalPrefix(description, zeroBasedIndex);
    }
}
