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
import mage.client.bridge.processor.BridgeGameLogRefresher;
import mage.client.bridge.processor.BridgeManaPlanEntry;
import mage.client.bridge.processor.BridgePassPriorityFlow;
import mage.client.bridge.processor.BridgePassPriorityFlowManager;
import mage.client.bridge.processor.BridgeProcessor;
import mage.client.bridge.processor.BridgeProcessorState;
import mage.client.bridge.processor.BridgePublishedActionChoices;
import mage.client.bridge.processor.BridgePublishedQueryBuilder;
import mage.client.bridge.processor.BridgePublishedQueryState;
import mage.client.bridge.processor.BridgeQueryCommandService;
import mage.client.bridge.processor.BridgeStartGameFlow;
import mage.client.bridge.processor.BridgeStartGameFlowManager;
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
import java.util.regex.Pattern;

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

    // Regex patterns to detect colored mana symbols inside braces, including hybrid/phyrexian variants.
    // Same approach as ManaUtil.java — \x7b = {, \x7d = }, .{0,2} allows up to 2 chars on each side.
    // Matches {W}, {W/U}, {W/P}, {W/U/P}, {2/W}, {C/W}, etc.
    private static final Pattern REGEX_WHITE = Pattern.compile("\\x7b.{0,2}W.{0,2}\\x7d");
    private static final Pattern REGEX_BLUE = Pattern.compile("\\x7b.{0,2}U.{0,2}\\x7d");
    private static final Pattern REGEX_BLACK = Pattern.compile("\\x7b.{0,2}B.{0,2}\\x7d");
    private static final Pattern REGEX_RED = Pattern.compile("\\x7b.{0,2}R.{0,2}\\x7d");
    private static final Pattern REGEX_GREEN = Pattern.compile("\\x7b.{0,2}G.{0,2}\\x7d");
    private static final Pattern REGEX_COLORLESS = Pattern.compile("\\x7b.{0,2}C.{0,2}\\x7d");

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
    private final BridgeGameLogRefresher gameLogRefresher;
    private final BridgeActionCommandService actionCommandService;
    private final BridgeQueryCommandService queryCommandService;
    private volatile Session session;
    private final ShortIdRegistry shortIds = new ShortIdRegistry("l");
    private static final int MAX_POOL_MANA_ATTEMPTS = 10; // Cancel payment after this many pool retries
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

    private record TargetChoice(UUID targetId, Map<String, Object> entry, CardView cardView) {
    }
    private enum DecisionBoundaryStatus {
        READY,
        AUTO_HANDLED,
        CHANGED
    }
    private enum NonDecisionActionStatus {
        NOT_HANDLED,
        AUTO_HANDLED,
        CHANGED
    }
    private record DecisionBoundaryTransition(DecisionBoundaryStatus status, PendingAction action) {
    }
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
            queryCommandService
        );
        this.chooseActionFlowManager = new BridgeChooseActionFlowManager(
            processor,
            client.getUsername(),
            processorState.decisionState(),
            new BridgeChooseActionFlowContextImpl(this, processorState),
            this::chooseActionDeliveryErrorResult
        );
        this.passPriorityFlowManager = new BridgePassPriorityFlowManager(
            processor,
            client.getUsername(),
            processorState.decisionState(),
            new BridgePassPriorityFlowContextImpl(this, processorState)
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
            this::executeDefaultActionImpl,
            this::attachUnseenChat,
            this::attachUnseenChat
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

    private void updateLastGameView(GameView gameView, String source) {
        processorState.gameState().updateLastGameView(gameView, source, logger, client.getUsername());
    }

    String username() {
        return client.getUsername();
    }

    boolean clientRunning() {
        return client.isRunning();
    }

    ChooseActionTool.Result noPendingChooseActionResult() {
        var result = new ChooseActionTool.Result();
        return buildError(result, "no_pending_action", "No pending action (game over or shutting down)", false, null);
    }

    UUID resolveShortId(String shortId) {
        return shortIds.resolve(shortId);
    }

    Set<UUID> validTargets(PendingAction action) {
        if (!(action.data() instanceof GameClientMessage targetMsg)) {
            return null;
        }
        return findValidTargets(targetMsg);
    }

    void finishChooseActionWithNextDecision(
            ChooseActionTool.Result result,
            PendingAction previousAction,
            PendingAction nextAction) {
        result.game_seq = nextAction.gameSeq();
        mergeActionChoices(result, null, nextAction);
        String summary = "after=" + summarizePendingAction(previousAction)
            + ",woke_to=" + summarizePendingAction(nextAction)
            + ",gameOver=" + processorState.gameState().gameOverObserved();
        logger.info("[" + client.getUsername() + "] chooseAction wakeup: " + summary);
        logBridgeEvent("CHOOSE_ACTION_WAKEUP", nextAction.gameId(), summary);
    }

    void finishChooseActionWithoutNextDecision(
            ChooseActionTool.Result result,
            PendingAction previousAction) {
        String summary = "after=" + summarizePendingAction(previousAction)
            + ",woke_to=game_over"
            + ",playerDead=" + processorState.gameState().playerDead()
            + ",activeGame=" + processorState.gameState().hasActiveGame()
            + ",clientRunning=" + client.isRunning();
        logger.info("[" + client.getUsername() + "] chooseAction wakeup: " + summary);
        logBridgeEvent("CHOOSE_ACTION_WAKEUP", previousAction.gameId(), summary);
        attachUnseenChat(result);
    }

    void finishBatchChooseActionWithNextDecision(
            ChooseActionTool.Result result,
            PendingAction nextAction) {
        result.game_seq = nextAction.gameSeq();
        mergeActionChoices(result, null, nextAction);
    }

    void finishBatchChooseActionWithoutNextDecision(ChooseActionTool.Result result) {
        attachUnseenChat(result);
    }

    ChooseActionTool.Result cancelledChooseActionResult(
            PendingAction previousAction,
            ChooseActionTool.Result partialResult) {
        ChooseActionTool.Result result = partialResult != null ? partialResult : new ChooseActionTool.Result();
        result.success = false;
        result.error = "Cancelled while waiting for choose_action";
        result.error_code = "cancelled";
        result.retryable = false;
        attachUnseenChat(result);
        if (previousAction != null) {
            String summary = "after=" + summarizePendingAction(previousAction) + ",woke_to=cancelled";
            logger.info("[" + client.getUsername() + "] chooseAction wakeup: " + summary);
            logBridgeEvent("CHOOSE_ACTION_WAKEUP", previousAction.gameId(), summary);
        }
        return result;
    }

    PendingAction resolvePassPriorityAction(PendingAction action) {
        DecisionBoundaryTransition transition =
            transitionToDecisionBoundary(action, "passPriority");
        return transition.status() == DecisionBoundaryStatus.READY ? transition.action() : null;
    }

    GameView preparePassPriorityActionView(PendingAction action) {
        if (action.data() instanceof GameClientMessage gcm) {
            GameView gv = gcm.getGameView();
            if (gv != null) {
                updateLastGameView(gv, "passPriority:" + action.method().name());
                processorState.interactionState().advanceTurn(gv);
                return gv;
            }
        }
        return processorState.gameState().lastGameView();
    }

    int interactionsThisTurn() {
        return processorState.interactionState().interactionsThisTurn();
    }

    int maxInteractionsPerTurn() {
        return processorState.interactionState().maxInteractionsPerTurn();
    }

    int lastTurnNumber() {
        return processorState.interactionState().lastTurnNumber();
    }

    void declareZombieGame(long absoluteIdleMs) {
        logger.error("[" + client.getUsername() + "] Zombie game detected: "
            + "no actionable callback for " + absoluteIdleMs + "ms, declaring game dead");
        logError("Zombie game detected: no actionable callback for " + absoluteIdleMs + "ms");
        processorState.gameState().markPlayerDead();
    }

    boolean failedManaCast(UUID objectId) {
        return processorState.interactionState().failedManaCast(objectId);
    }

    void finalizePassPriorityResult(
            BridgePassPriorityFlow flow,
            String until,
            int actionsPassed,
            PendingAction action,
            GameView view,
            ActionResult result,
            boolean actionPending) {
        processorState.decisionState().clearPendingPassPriorityFlowIfCurrent(flow);
        attachUnseenChat(result);
        logPassPriorityReturn(until, actionsPassed, action, view, result, actionPending);
    }

    /**
     * Send a boolean response to the server, or declare the player dead if it fails.
     *
     * SessionImpl.sendPlayerBoolean() can return false silently (session expired,
     * RMI failure, etc.).  When that happens the server never gets the response
     * and the game deadlocks: the server waits for an answer that was never
     * delivered, and the bridge waits for a callback that will never arrive.
     *
     * On CI runners under memory pressure this causes 120s golden-test timeouts.
     * Detecting the failure immediately lets the wait loops exit cleanly instead
     * of blocking until the HTTP socket times out.
     */
    void sendBooleanOrDie(UUID gameId, boolean data, String context) {
        boolean ok = session.sendPlayerBoolean(gameId, data);
        if (!ok) {
            declareResponseFailed("sendPlayerBoolean(" + data + ")", context, gameId);
        }
    }

    void sendUuidOrDie(UUID gameId, UUID data, String context) {
        boolean ok = session.sendPlayerUUID(gameId, data);
        if (!ok) {
            declareResponseFailed("sendPlayerUUID(" + data + ")", context, gameId);
        }
    }

    void sendStringOrDie(UUID gameId, String data, String context) {
        boolean ok = session.sendPlayerString(gameId, data);
        if (!ok) {
            declareResponseFailed("sendPlayerString(" + data + ")", context, gameId);
        }
    }

    private void sendIntegerOrDie(UUID gameId, int data, String context) {
        boolean ok = session.sendPlayerInteger(gameId, data);
        if (!ok) {
            declareResponseFailed("sendPlayerInteger(" + data + ")", context, gameId);
        }
    }

    private void sendManaTypeOrDie(UUID gameId, UUID playerId, ManaType data, String context) {
        boolean ok = session.sendPlayerManaType(gameId, playerId, data);
        if (!ok) {
            declareResponseFailed("sendPlayerManaType(" + data + ")", context, gameId);
        }
    }

    /**
     * Unchecked exception thrown when a sendPlayer* call fails.
     * Prevents callers from continuing on the success path after a dropped response.
     */
    public static class ResponseDeliveryException extends RuntimeException {
        ResponseDeliveryException(String message) {
            super(message);
        }
    }

    private void declareResponseFailed(String call, String context, UUID gameId) {
        String msg = call + " failed — server did not receive response"
            + " (context=" + context + ", gameId=" + gameId + ")";
        logger.error("[" + client.getUsername() + "] CRITICAL: " + msg);
        logError(msg);
        processorState.gameState().markPlayerDead();
        throw new ResponseDeliveryException(msg);
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

    /**
     * Write a bridge event to the JSONL dump file (data hoarding).
     * Each line is a compact JSON object with timestamp, callback method, and relevant data.
     */
    private void logBridgeEvent(String method, String summary) {
        logBridgeEvent(method, processorState.gameState().currentGameId(), summary);
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

    private static String abbreviateForLog(String value, int maxChars) {
        if (value == null) {
            return "null";
        }
        String normalized = value.replace('\n', ' ').replace('\r', ' ');
        if (normalized.length() <= maxChars) {
            return normalized;
        }
        return normalized.substring(0, Math.max(0, maxChars - 3)) + "...";
    }

    private String summarizePendingAction(PendingAction action) {
        if (action == null) {
            return "none";
        }
        return "method=" + action.method().name()
            + ",gameId=" + action.gameId()
            + ",gameSeq=" + action.gameSeq()
            + ",message=" + abbreviateForLog(action.message(), 120);
    }

    private String gameViewStep(GameView gameView) {
        if (gameView == null || gameView.getStep() == null) {
            return "null";
        }
        return gameView.getStep().toString();
    }

    private void logPassPriorityReturn(
            String until,
            int actionsPassed,
            PendingAction action,
            GameView gameView,
            ActionResult result,
            boolean returnedChoices) {
        String actionMethod = action != null ? action.method().name() : "none";
        String actionGameId = action != null ? String.valueOf(action.gameId()) : "null";
        int callbackGameSeq = action != null ? action.gameSeq() : -1;
        String step = gameViewStep(gameView);
        String summary = "until=" + until
            + ",stop_reason=" + result.stop_reason
            + ",actionsPassed=" + actionsPassed
            + ",callbackMethod=" + actionMethod
            + ",callbackGameId=" + actionGameId
            + ",callbackGameSeq=" + callbackGameSeq
            + ",step=" + step
            + ",autoPassedBeforeReturn=" + (actionsPassed > 0)
            + ",returnedChoices=" + returnedChoices
            + ",pendingAction=" + summarizePendingAction(processorState.decisionState().pendingAction());
        logger.info("[" + client.getUsername() + "] passPriority RETURN: " + summary);
        logBridgeEvent("PASS_PRIORITY_RETURN", action != null ? action.gameId() : processorState.gameState().currentGameId(), summary);
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

    // Visible for tests that need to wait for queued callback processing.
    void awaitProcessorIdle() {
        processor.submit(new BridgeCommand<Void>() {
            @Override
            public Void execute() {
                return null;
            }
        });
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

    private Map<String, Object> executeDefaultActionImpl() {
        var result = new HashMap<String, Object>();
        PendingAction action = processorState.decisionState().pendingAction();
        if (action == null) {
            result.put("success", false);
            result.put("error", "No pending action");
            attachUnseenChat(result);
            return result;
        }

        // Clear pending action only if it hasn't been overwritten by a new callback.
        processorState.decisionState().clearPendingActionIfCurrent(action);

        // Execute the default response based on action type
        UUID gameId = action.gameId();
        ClientCallbackMethod method = action.method();
        Object data = action.data();

        result.put("success", true);
        result.put("action_type", method.name());

        switch (method) {
            case GAME_ASK, GAME_SELECT -> {
                sendBooleanOrDie(gameId, false, "defaultAction:" + method.name());
                result.put("action_taken", "passed_priority");
            }
            case GAME_PLAY_MANA, GAME_PLAY_XMANA -> {
                // Auto-tap failed; default action is to cancel the spell
                sendBooleanOrDie(gameId, false, "defaultAction:" + method.name());
                result.put("action_taken", "cancelled_mana");
            }
            case GAME_TARGET -> {
                GameClientMessage targetMsg = (GameClientMessage) data;
                boolean required = targetMsg.isFlag();
                // Try to find valid targets from multiple sources
                Set<UUID> targets = findValidTargets(targetMsg);
                if (required && targets != null && !targets.isEmpty()) {
                    UUID firstTarget = selectDeterministicTarget(targets, null);
                    sendUuidOrDie(gameId, firstTarget, "defaultAction:GAME_TARGET");
                    result.put("action_taken", "selected_first_target");
                } else {
                    sendBooleanOrDie(gameId, false, "defaultAction:GAME_TARGET_cancel");
                    result.put("action_taken", "cancelled");
                }
            }
            case GAME_CHOOSE_ABILITY -> {
                AbilityPickerView picker = (AbilityPickerView) data;
                Map<UUID, String> abilityChoices = picker.getChoices();
                if (abilityChoices != null && !abilityChoices.isEmpty()) {
                    UUID firstChoice = abilityChoices.keySet().iterator().next();
                    sendUuidOrDie(gameId, firstChoice, "defaultAction:GAME_CHOOSE_ABILITY");
                    result.put("action_taken", "selected_first_ability");
                } else {
                    sendUuidOrDie(gameId, null, "defaultAction:GAME_CHOOSE_ABILITY_null");
                    result.put("action_taken", "no_abilities");
                }
            }
            case GAME_CHOOSE_CHOICE -> {
                GameClientMessage choiceMsg = (GameClientMessage) data;
                Choice choice = choiceMsg.getChoice();
                if (choice != null) {
                    if (choice.isKeyChoice()) {
                        Map<String, String> keyChoices = choice.getKeyChoices();
                        if (keyChoices != null && !keyChoices.isEmpty()) {
                            String firstKey = keyChoices.keySet().iterator().next();
                            sendStringOrDie(gameId, firstKey, "defaultAction:GAME_CHOOSE_CHOICE_key");
                            result.put("action_taken", "selected_first_key_choice");
                        } else {
                            sendStringOrDie(gameId, null, "defaultAction:GAME_CHOOSE_CHOICE_null");
                            result.put("action_taken", "no_choices");
                        }
                    } else {
                        Set<String> choices = choice.getChoices();
                        if (choices != null && !choices.isEmpty()) {
                            String firstChoice = choices.iterator().next();
                            sendStringOrDie(gameId, firstChoice, "defaultAction:GAME_CHOOSE_CHOICE");
                            result.put("action_taken", "selected_first_choice");
                        } else {
                            sendStringOrDie(gameId, null, "defaultAction:GAME_CHOOSE_CHOICE_null");
                            result.put("action_taken", "no_choices");
                        }
                    }
                } else {
                    sendStringOrDie(gameId, null, "defaultAction:GAME_CHOOSE_CHOICE_null");
                    result.put("action_taken", "null_choice");
                }
            }
            case GAME_CHOOSE_PILE -> {
                sendBooleanOrDie(gameId, true, "defaultAction:GAME_CHOOSE_PILE");
                result.put("action_taken", "selected_pile_1");
            }
            case GAME_GET_AMOUNT -> {
                GameClientMessage amountMsg = (GameClientMessage) data;
                int min = amountMsg.getMin();
                sendIntegerOrDie(gameId, min, "defaultAction:GAME_GET_AMOUNT");
                result.put("action_taken", "selected_min_amount");
                result.put("amount", min);
            }
            case GAME_GET_MULTI_AMOUNT -> {
                GameClientMessage multiMsg = (GameClientMessage) data;
                var sb = new StringBuilder();
                if (multiMsg.getMessages() != null) {
                    for (int i = 0; i < multiMsg.getMessages().size(); i++) {
                        if (i > 0) sb.append(" ");
                        sb.append(multiMsg.getMessages().get(i).defaultValue);
                    }
                }
                sendStringOrDie(gameId, sb.toString(), "defaultAction:GAME_GET_MULTI_AMOUNT");
                result.put("action_taken", "selected_default_multi_amount");
            }
            default -> {
                result.put("success", false);
                result.put("error", "Unknown action type: " + method);
            }
        }

        attachUnseenChat(result);
        return result;
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

    private ActionResult buildActionChoices(PendingAction action, Long boardCursorParam) {
        return publishedQueryBuilder.buildActionChoices(action, boardCursorParam);
    }

    boolean clearPendingActionIfCurrent(PendingAction action) {
        return processorState.decisionState().clearPendingActionIfCurrent(action);
    }

    private DecisionBoundaryTransition transitionToDecisionBoundary(PendingAction action, String source) {
        if (action == null) {
            return new DecisionBoundaryTransition(DecisionBoundaryStatus.CHANGED, null);
        }
        NonDecisionActionStatus nonDecisionStatus = maybeAutoHandleNonDecisionAction(action, source);
        if (nonDecisionStatus == NonDecisionActionStatus.AUTO_HANDLED) {
            return new DecisionBoundaryTransition(DecisionBoundaryStatus.AUTO_HANDLED, null);
        }
        if (nonDecisionStatus == NonDecisionActionStatus.CHANGED) {
            return new DecisionBoundaryTransition(DecisionBoundaryStatus.CHANGED, null);
        }
        if (processorState.decisionState().pendingAction() != action) {
            return new DecisionBoundaryTransition(DecisionBoundaryStatus.CHANGED, null);
        }
        return new DecisionBoundaryTransition(DecisionBoundaryStatus.READY, action);
    }

    private NonDecisionActionStatus maybeAutoHandleNonDecisionAction(PendingAction action, String source) {
        if (action.method() == ClientCallbackMethod.GAME_PLAY_MANA
                || action.method() == ClientCallbackMethod.GAME_PLAY_XMANA) {
            return maybeAutoHandlePendingManaAction(action, source);
        }
        if (action.method() != ClientCallbackMethod.GAME_TARGET
                && action.method() != ClientCallbackMethod.GAME_CHOOSE_ABILITY) {
            return NonDecisionActionStatus.NOT_HANDLED;
        }

        // --- GAME_TARGET auto-handling ---
        if (action.method() == ClientCallbackMethod.GAME_TARGET) {
            return maybeAutoHandleGameTarget(action, source);
        }

        // --- GAME_CHOOSE_ABILITY auto-handling ---
        return maybeAutoHandleGameChooseAbility(action, source);
    }

    private NonDecisionActionStatus maybeAutoHandleGameTarget(PendingAction action, String source) {
        GameClientMessage targetMsg = (GameClientMessage) action.data();
        Set<UUID> targets = findValidTargets(targetMsg);
        boolean required = targetMsg.isFlag();

        if (!required && (targets == null || targets.isEmpty())) {
            if (clearPendingActionIfCurrent(action)) {
                logger.info("[" + client.getUsername() + "] " + source
                    + ": auto-cancelling optional GAME_TARGET with no valid targets");
                processorState.decisionState().clearLastChoices();
                processorState.decisionState().clearChoiceSnapshot();
                sendBooleanOrDie(action.gameId(), false, "auto-cancel optional GAME_TARGET");
                return NonDecisionActionStatus.AUTO_HANDLED;
            }
            return processorState.decisionState().pendingAction() != action
                ? NonDecisionActionStatus.CHANGED
                : NonDecisionActionStatus.NOT_HANDLED;
        }

        UUID onlyTarget = selectSingleRequiredTarget(targetMsg);
        if (onlyTarget == null) {
            return NonDecisionActionStatus.NOT_HANDLED;
        }

        if (clearPendingActionIfCurrent(action)) {
            logger.info("[" + client.getUsername() + "] " + source
                + ": auto-selecting single required GAME_TARGET " + onlyTarget.toString().substring(0, 8));
            GameView gv = targetMsg.getGameView();
            updateLastGameView(gv, source + ":single_required_target");
            processorState.decisionState().clearLastChoices();
            processorState.decisionState().clearChoiceSnapshot();
            sendUuidOrDie(action.gameId(), onlyTarget, "auto-select single required GAME_TARGET");
            return NonDecisionActionStatus.AUTO_HANDLED;
        }
        return processorState.decisionState().pendingAction() != action
            ? NonDecisionActionStatus.CHANGED
            : NonDecisionActionStatus.NOT_HANDLED;
    }

    private NonDecisionActionStatus maybeAutoHandlePendingManaAction(PendingAction action, String source) {
        if (!clearPendingActionIfCurrent(action)) {
            return processorState.decisionState().pendingAction() != action
                ? NonDecisionActionStatus.CHANGED
                : NonDecisionActionStatus.NOT_HANDLED;
        }

        try {
            processorState.decisionState().clearLastChoices();
            processorState.decisionState().clearChoiceSnapshot();
            boolean handled = handleGamePlayManaAuto(action.gameId(), (GameClientMessage) action.data());
            if (handled) {
                logger.info("[" + client.getUsername() + "] " + source
                    + ": auto-resolved pending " + action.method().name() + " at decision boundary");
                return NonDecisionActionStatus.AUTO_HANDLED;
            }
        } catch (ResponseDeliveryException e) {
            throw e;
        } catch (Exception e) {
            logError("Pending mana auto-handler exception: " + e.getMessage());
            logger.debug("[" + client.getUsername() + "] Pending mana auto-handler stack trace", e);
        }

        processorState.decisionState().restorePendingActionIfEmpty(action);
        return processorState.decisionState().pendingAction() != action
            ? NonDecisionActionStatus.CHANGED
            : NonDecisionActionStatus.NOT_HANDLED;
    }

    private NonDecisionActionStatus maybeAutoHandleGameChooseAbility(PendingAction action, String source) {
        AbilityPickerView picker = (AbilityPickerView) action.data();
        Map<UUID, String> choices = picker.getChoices();

        // No choices: auto-send null
        if (choices == null || choices.isEmpty()) {
            if (clearPendingActionIfCurrent(action)) {
                logger.warn("[" + client.getUsername() + "] " + source
                    + ": auto-selecting ability: no choices, sending null");
                processorState.decisionState().clearLastChoices();
                processorState.decisionState().clearChoiceSnapshot();
                sendUuidOrDie(action.gameId(), null, "auto GAME_CHOOSE_ABILITY null_choice");
                return NonDecisionActionStatus.AUTO_HANDLED;
            }
            return processorState.decisionState().pendingAction() != action
                ? NonDecisionActionStatus.CHANGED
                : NonDecisionActionStatus.NOT_HANDLED;
        }

        // Mana plan active: consume ability index and select
        if (processorState.interactionState().manaPlan() != null) {
            if (clearPendingActionIfCurrent(action)) {
                Integer abilityIdx = processorState.interactionState().consumeManaPlanAbilityIndex();
                UUID selected;
                if (abilityIdx != null) {
                    List<UUID> abilityIds = new ArrayList<>(choices.keySet());
                    if (abilityIdx >= 0 && abilityIdx < abilityIds.size()) {
                        selected = abilityIds.get(abilityIdx);
                        logger.info("[" + client.getUsername() + "] " + source
                            + ": mana plan selecting ability " + abilityIdx + ": \""
                            + picker.getMessage() + "\" -> " + choices.get(selected));
                    } else {
                        // Bad ability index: send null to satisfy the UUID callback,
                        // then clean up mana plan state.
                        logger.warn("[" + client.getUsername() + "] " + source
                            + ": mana plan ability index " + abilityIdx
                            + " out of range (0-" + (abilityIds.size() - 1) + ") for \""
                            + picker.getMessage() + "\", cancelling spell");
                        processorState.interactionState().clearManaPlan();
                        processorState.gameLogState().addSystemMessage("[System] Spell cancelled — mana plan ability index was incorrect.");
                        logBridgeEvent("SPELL_CANCELLED", "mana plan ability index out of range");
                        processorState.decisionState().clearLastChoices();
                        processorState.decisionState().clearChoiceSnapshot();
                        sendUuidOrDie(action.gameId(), null,
                            "auto GAME_CHOOSE_ABILITY bad_mana_plan");
                        return NonDecisionActionStatus.AUTO_HANDLED;
                    }
                } else {
                    // No explicit ability index: pick first
                    selected = choices.keySet().iterator().next();
                    if (choices.size() == 1) {
                        logger.info("[" + client.getUsername() + "] " + source
                            + ": mana plan auto-selecting sole ability: \""
                            + picker.getMessage() + "\" -> " + choices.get(selected));
                    } else {
                        logger.info("[" + client.getUsername() + "] " + source
                            + ": mana plan no ability index, picking first of "
                            + choices.size() + ": \"" + picker.getMessage()
                            + "\" -> " + choices.get(selected));
                    }
                }
                processorState.decisionState().clearLastChoices();
                processorState.decisionState().clearChoiceSnapshot();
                sendUuidOrDie(action.gameId(), selected,
                    "auto GAME_CHOOSE_ABILITY mana_plan");
                return NonDecisionActionStatus.AUTO_HANDLED;
            }
            return processorState.decisionState().pendingAction() != action
                ? NonDecisionActionStatus.CHANGED
                : NonDecisionActionStatus.NOT_HANDLED;
        }

        // Has choices, no mana plan: let the LLM decide
        return NonDecisionActionStatus.NOT_HANDLED;
    }

    private void logChoiceOutOfRangeDiagnostic(ClientCallbackMethod method, Integer index, List<Object> choices) {
        long generatedAtMs = processorState.decisionState().lastChoicesGeneratedAtMs();
        long ageMs = generatedAtMs == 0 ? -1 : System.currentTimeMillis() - generatedAtMs;
        PendingAction nowPending = processorState.decisionState().pendingAction();
        String nowPendingType = nowPending == null ? "none" : nowPending.method().name();
        logger.warn("[" + client.getUsername() + "] choose_action out-of-range diagnostic: "
                + "method=" + method.name()
                + ", index=" + index
                + ", choices_size=" + (choices == null ? -1 : choices.size())
                + ", pending_now=" + nowPendingType
                + ", last_choices_action=" + (processorState.decisionState().lastChoicesActionType() == null ? "none" : processorState.decisionState().lastChoicesActionType())
                + ", last_choices_response="
                + (processorState.decisionState().lastChoicesResponseType() == null ? "none" : processorState.decisionState().lastChoicesResponseType())
                + ", last_choices_count=" + processorState.decisionState().lastChoicesCount()
                + ", last_choices_age_ms=" + ageMs);
    }

    /**
     * When choose_action fails validation, attach the available choices to the error response
     * so the model can self-correct without a separate get_action_choices round trip.
     */
    private void attachChoicesToError(ChooseActionTool.Result errorResult, PendingAction action) {
        ActionResult choicesResult = buildActionChoices(action, null);
        if (choicesResult.choices != null) {
            errorResult.choices = choicesResult.choices;
        }
    }

    /**
     * Build a standardized error response for choose_action failures.
     * Must reuse the caller's result map so the finally block can read success=false.
     */
    ChooseActionTool.Result buildError(ChooseActionTool.Result result, String errorCode,
            String message, boolean retryable, PendingAction action, boolean attachChoices) {
        result.success = false;
        result.error = message;
        result.error_code = errorCode;
        result.retryable = retryable;
        processorState.decisionState().restorePendingAction(action);
        if (attachChoices) {
            attachChoicesToError(result, action);
        }
        attachUnseenChat(result);
        return result;
    }

    ChooseActionTool.Result buildError(ChooseActionTool.Result result, String errorCode,
            String message, boolean retryable, PendingAction action) {
        return buildError(result, errorCode, message, retryable, action, false);
    }

    private static String formatAmountRange(int min, int max) {
        if (min == max) {
            return Integer.toString(min);
        }
        return min + "-" + max;
    }

    static String validateMultiAmountInput(GameClientMessage msg, int[] amounts) {
        List<MultiAmountMessage> items = msg.getMessages();
        if (items == null) {
            throw new IllegalStateException("GAME_GET_MULTI_AMOUNT is missing item metadata");
        }

        int expectedCount = items.size();
        String expectedEntries = expectedCount + " " + (expectedCount == 1 ? "entry" : "entries");
        String expectedShape = "Expected " + expectedCount + " amount"
            + (expectedCount == 1 ? "" : "s")
            + " and total " + formatAmountRange(msg.getMin(), msg.getMax()) + ".";

        if (amounts.length != expectedCount) {
            return "Invalid 'amounts' for GAME_GET_MULTI_AMOUNT: expected " + expectedEntries
                + ", got " + amounts.length + ". " + expectedShape;
        }

        long total = 0;
        for (int i = 0; i < expectedCount; i++) {
            MultiAmountMessage item = items.get(i);
            int value = amounts[i];
            total += value;
            if (value < item.min || value > item.max) {
                return "Invalid 'amounts' for GAME_GET_MULTI_AMOUNT: amounts[" + i + "]=" + value
                    + " is outside item range " + formatAmountRange(item.min, item.max)
                    + ". " + expectedShape;
            }
        }

        if (total < msg.getMin() || total > msg.getMax()) {
            return "Invalid 'amounts' for GAME_GET_MULTI_AMOUNT: total " + total
                + " is outside allowed range " + formatAmountRange(msg.getMin(), msg.getMax())
                + ". " + expectedShape;
        }

        return null;
    }

    /**
     * Respond to the current pending action with a specific choice.
     * Exactly one parameter should be non-null, matching the response_type from getActionChoices().
     */
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

    private static BridgeChooseActionStartResult chooseActionDone(ChooseActionTool.Result result) {
        return new BridgeChooseActionStartResult(result, false);
    }

    private static BridgeChooseActionStartResult chooseActionAwaitNextDecision(ChooseActionTool.Result result) {
        return new BridgeChooseActionStartResult(result, true);
    }

    BridgeChooseActionStartResult applyChooseActionNow(
            BridgeChooseActionInput input,
            PendingAction action) {
        var result = new ChooseActionTool.Result();
        result.game_seq = action.gameSeq();
        // Local copies of parameters that may be nulled/reassigned during validation
        Integer resolvedIndex = input.index();
        String[] effectiveManaPlan = input.manaPlan();
        String id = input.id();
        Boolean answer = input.answer();
        Integer amount = input.amount();
        int[] amounts = input.amounts();
        Integer pile = input.pile();
        String text = input.text();
        Boolean autoTap = input.autoTap();

        // Loop detection: model has made too many interactions this turn — auto-handle
        if (processorState.interactionState().interactionsThisTurn() > processorState.interactionState().maxInteractionsPerTurn()) {
            logger.warn("[" + client.getUsername() + "] Loop detected (" + processorState.interactionState().interactionsThisTurn()
                + " interactions this turn), auto-handling " + action.method().name());
            // Not a critical error — LLM is stuck in a loop, not a code bug
            try {
                executeDefaultAction();
            } catch (ResponseDeliveryException e) {
                result.success = false;
                result.error = e.getMessage();
                result.error_code = "response_delivery_failed";
                result.retryable = false;
                attachUnseenChat(result);
                return chooseActionDone(result);
            }
            result.success = true;
            result.action_taken = "auto_passed_loop_detected";
            result.warning = "Too many interactions this turn (" + processorState.interactionState().interactionsThisTurn() + "). Auto-passing until next turn.";
            return chooseActionDone(result);
        }

        ClientCallbackMethod method = action.method();

        // Resolve id to index for action types that accept short IDs.
        // GAME_CHOOSE_CHOICE uses text=Name or choice=N, so free-form strings
        // like "Black" must reach the action-specific validation instead.
        if (id != null && method != ClientCallbackMethod.GAME_CHOOSE_CHOICE) {
            if (resolvedIndex != null) {
                // Both provided — prefer id (it's more specific; index is usually a default value)
                logger.warn("[" + client.getUsername() + "] choose_action: both id=" + id + " and index=" + resolvedIndex + " provided, preferring id");
                result.warning = "Both id and index provided; used id=" + id + ", ignored index=" + resolvedIndex;
                resolvedIndex = null;
            }
            List<Object> choices = processorState.decisionState().lastChoices();
            if (choices == null) {
                buildActionChoices(action, null);
                choices = processorState.decisionState().lastChoices();
            }
            if ("all".equals(id)) {
                // Find the "special" entry in lastChoices
                if (choices != null) {
                    for (int i = 0; i < choices.size(); i++) {
                        if ("special".equals(choices.get(i))) {
                            resolvedIndex = i;
                            break;
                        }
                    }
                }
                if (resolvedIndex == null) {
                    return chooseActionDone(buildError(result, "invalid_choice",
                        "\"all\" is not available in current choices", true, action, true));
                }
            } else {
                UUID resolvedUuid = shortIds.tryResolve(id);
                if (resolvedUuid == null) {
                    return chooseActionDone(buildError(result, "invalid_choice",
                        "Unknown short ID: " + id + ". Call get_action_choices to see current options.",
                        true, action, true));
                }
                if (choices != null) {
                    for (int i = 0; i < choices.size(); i++) {
                        if (resolvedUuid.equals(choices.get(i))) {
                            resolvedIndex = i;
                            break;
                        }
                    }
                }
                if (resolvedIndex == null) {
                    return chooseActionDone(buildError(result, "invalid_choice",
                        "Object " + id + " not found in current choices", true, action, true));
                }
            }
        }

        // Normalize empty mana_plan to null
        if (effectiveManaPlan != null && effectiveManaPlan.length == 0) {
            effectiveManaPlan = null;
        }

        // Auto-populate choices if the model skipped get_action_choices.
        // Use the captured action directly so the choice snapshot matches the
        // decision we're answering even if pendingAction changes concurrently.
        if (resolvedIndex != null && processorState.decisionState().lastChoices() == null) {
            logger.info("[" + client.getUsername() + "] choose_action: auto-populating choices (get_action_choices was not called)");
            buildActionChoices(action, null);
        }

        // Clear pending action only if it hasn't been overwritten by a new callback.
        // Without this CAS, a callback arriving between our read and this write would be lost.
        processorState.decisionState().clearPendingActionIfCurrent(action);

        UUID gameId = action.gameId();
        Object data = action.data();

        result.success = true;

        try {
            switch (method) {
                case GAME_ASK:
                    // GAME_ASK is boolean-only; ignore index if also provided
                    // (some models send all params with defaults)
                    if (answer == null) {
                        return chooseActionDone(buildError(result, "missing_param",
                            "GAME_ASK requires choice=\"yes\" or choice=\"no\". "
                            + "This is a yes/no question.", true, action));
                    }
                    if (resolvedIndex != null) {
                        logger.warn("[" + client.getUsername() + "] choose_action: ignoring index=" + resolvedIndex + " for GAME_ASK (boolean-only)");
                    }
                    sendBooleanOrDie(gameId, answer, "chooseAction:GAME_ASK");
                    result.action_taken = answer ? "yes" : "no";
                    break;

                case GAME_SELECT: {
                    // Support both index (play a card) and answer (pass priority).
                    // When both are provided (some models send all params with defaults),
                    // try index first but fall through to answer if index is invalid.
                    boolean usedIndex = false;
                    if (resolvedIndex != null) {
                        List<Object> choices = processorState.decisionState().lastChoices();
                        if (choices == null || resolvedIndex < 0 || resolvedIndex >= choices.size()) {
                            logChoiceOutOfRangeDiagnostic(method, resolvedIndex, choices);
                            // Index is invalid — if answer is also available, fall through
                            if (answer != null) {
                                logger.warn("[" + client.getUsername() + "] choose_action: index " + resolvedIndex
                                    + " out of range, falling through to answer=" + answer + " for GAME_SELECT");
                            } else {
                                return chooseActionDone(buildError(result, "index_out_of_range",
                                    "Index " + resolvedIndex + " is out of range"
                                    + (choices != null ? " (valid: 0-" + (choices.size() - 1) + ")" : " (no choices loaded — call get_action_choices first)")
                                    + ". Call get_action_choices to see current options.", true, action, true));
                            }
                        } else {
                            Object chosen = choices.get(resolvedIndex);
                            if (chosen instanceof UUID chosenUuid) {
                                // Validate mana plan before sending spell to server —
                                // once sent, cancellation is async and confuses the model
                                if (effectiveManaPlan != null) {
                                    CopyOnWriteArrayList<BridgeManaPlanEntry> parsedPlan;
                                    try {
                                        parsedPlan = parseManaPlan(effectiveManaPlan);
                                    } catch (IllegalArgumentException e) {
                                        return chooseActionDone(buildError(result, "invalid_mana_plan",
                                            "Invalid mana_plan: " + e.getMessage()
                                            + ". Expected: [\"p1\",\"p2:0\",\"RED\"]", true, action));
                                    }
                                    for (BridgeManaPlanEntry entry : parsedPlan) {
                                        if ("tap".equals(entry.type()) && shortIds.tryResolve(entry.value()) == null) {
                                            return chooseActionDone(buildError(result, "invalid_mana_plan",
                                                "Mana plan references unknown permanent '" + entry.value()
                                                + "'. Check the board state for correct permanent IDs.", true, action));
                                        }
                                    }
                                    processorState.interactionState().setManaPlan(parsedPlan, !(autoTap != null && !autoTap));
                                    result.mana_plan_set = true;
                                    result.mana_plan_size = parsedPlan.size();
                                } else if (autoTap != null && autoTap) {
                                    processorState.interactionState().clearManaPlan();  // Explicit auto-tap mode
                                }
                                sendUuidOrDie(gameId, chosenUuid, "chooseAction:GAME_SELECT_index");
                                result.action_taken = "selected_" + resolvedIndex;
                                usedIndex = true;
                            } else if (chosen instanceof String chosenStr) {
                                sendStringOrDie(gameId, chosenStr, "chooseAction:GAME_SELECT_special");
                                result.action_taken = "special_" + chosenStr;
                                usedIndex = true;
                            } else {
                                return chooseActionDone(buildError(result, "internal_error",
                                    "Unexpected choice type at index " + resolvedIndex, false, action));
                            }
                        }
                    }
                    if (!usedIndex) {
                        if (answer != null) {
                            sendBooleanOrDie(gameId, answer, "chooseAction:GAME_SELECT_answer");
                            result.action_taken = answer ? "confirmed" : "passed_priority";
                        } else {
                            return chooseActionDone(buildError(result, "missing_param",
                                "GAME_SELECT requires choice=pN to play a card, "
                                + "or choice=\"no\" to pass priority. Call get_action_choices first to see available cards.",
                                true, action, true));
                        }
                    }
                    break;
                }

                case GAME_PLAY_MANA:
                case GAME_PLAY_XMANA: {
                    // index = tap a mana source OR spend a mana type from pool, answer=false = cancel.
                    // When both are provided and index is invalid, fall through to answer.
                    boolean usedManaIndex = false;
                    if (resolvedIndex != null) {
                        List<Object> choices = processorState.decisionState().lastChoices();
                        if (choices == null || resolvedIndex < 0 || resolvedIndex >= choices.size()) {
                            logChoiceOutOfRangeDiagnostic(method, resolvedIndex, choices);
                            if (answer != null && !answer) {
                                logger.warn("[" + client.getUsername() + "] choose_action: index " + resolvedIndex
                                    + " out of range, falling through to cancel for GAME_PLAY_MANA");
                            } else {
                                return chooseActionDone(buildError(result, "index_out_of_range",
                                    "Index " + resolvedIndex + " is out of range"
                                    + (choices != null ? " (valid: 0-" + (choices.size() - 1) + ")" : " (no choices loaded — call get_action_choices first)")
                                    + ". Call get_action_choices to see current options.", true, action, true));
                            }
                        } else {
                            Object manaChoice = choices.get(resolvedIndex);
                            if (manaChoice instanceof UUID manaUuid) {
                                sendUuidOrDie(gameId, manaUuid, "chooseAction:GAME_PLAY_MANA");
                                result.action_taken = "tapped_mana_" + resolvedIndex;
                                usedManaIndex = true;
                            } else if (manaChoice instanceof ManaType manaType) {
                                UUID manaPlayerId = getManaPoolPlayerId(gameId, processorState.gameState().lastGameView());
                                if (manaPlayerId == null) {
                                    return chooseActionDone(buildError(result, "internal_error",
                                        "Could not resolve player ID for mana pool selection", false, action));
                                }
                                sendManaTypeOrDie(gameId, manaPlayerId, manaType, "chooseAction:GAME_PLAY_MANA_pool");
                                result.action_taken = "used_pool_" + manaType.toString();
                                usedManaIndex = true;
                            } else {
                                return chooseActionDone(buildError(result, "internal_error",
                                    "Unsupported mana choice type at index " + resolvedIndex, false, action));
                            }
                        }
                    }
                    if (!usedManaIndex) {
                        boolean cancel = false;
                        if (answer != null && !answer) {
                            cancel = true;
                        } else if (answer != null && answer) {
                            // answer=true with no mana sources: treat as cancel.
                            // When the choice list is empty, storePendingAction sends response_type "boolean".
                            // Models interpret this as a confirmation and send true, but cancel is the only option.
                            List<Object> choices = processorState.decisionState().lastChoices();
                            if (choices == null || choices.isEmpty()) {
                                logger.warn("[" + client.getUsername() + "] choose_action: answer=true for GAME_PLAY_MANA with no mana sources, auto-cancelling");
                                cancel = true;
                            }
                        }
                        if (cancel) {
                            // Mark spell as failed to prevent infinite retry loop
                            UUID payingForId = extractPayingForId(action.message());
                            if (payingForId != null) {
                                processorState.interactionState().markFailedManaCast(payingForId);
                            }
                            processorState.interactionState().clearManaPlan();
                            sendBooleanOrDie(gameId, false, "chooseAction:GAME_PLAY_MANA_cancel");
                            result.action_taken = "cancelled_spell";
                        } else {
                            return chooseActionDone(buildError(result, "missing_param",
                                "GAME_PLAY_MANA requires choice=pN to choose a mana source, or choice=\"no\" to cancel the spell. "
                                + "Call get_action_choices first to see available mana sources.", true, action, true));
                        }
                    }
                    break;
                }

                case GAME_TARGET: {
                    GameClientMessage targetMsg = (GameClientMessage) data;
                    boolean required = targetMsg.isFlag();

                    // Index takes priority over answer:false (models sometimes send both)
                    if (resolvedIndex != null) {
                        if (answer != null) {
                            logger.warn("[" + client.getUsername() + "] choose_action: ignoring answer=" + answer + " because index was also provided for GAME_TARGET");
                        }
                        List<Object> choices = processorState.decisionState().lastChoices();
                        if (choices != null && resolvedIndex >= 0 && resolvedIndex < choices.size()) {
                            UUID targetUUID = (UUID) choices.get(resolvedIndex);
                            sendUuidOrDie(gameId, targetUUID, "chooseAction:GAME_TARGET_index");
                            result.action_taken = "selected_target_" + resolvedIndex;
                            break;
                        }
                        logChoiceOutOfRangeDiagnostic(method, resolvedIndex, choices);
                        // Index out of range. For required targets, auto-select to avoid
                        // infinite retry loops. For optional targets, return an error so
                        // the model can retry with a valid index or answer=false.
                        if (!required) {
                            List<Object> targetChoices = processorState.decisionState().lastChoices();
                            return chooseActionDone(buildError(result, "index_out_of_range",
                                "Index " + resolvedIndex + " is out of range"
                                + (targetChoices != null ? " (valid: 0-" + (targetChoices.size() - 1) + ")" : " (no choices loaded — call get_action_choices first)")
                                + ". Call get_action_choices to see current targets.", true, action, true));
                        }
                        logger.warn("[" + client.getUsername() + "] choose_action: index " + resolvedIndex
                            + " out of range for required GAME_TARGET (choices="
                            + (choices == null ? "null" : choices.size()) + "), auto-selecting");
                    } else if (answer != null && !answer) {
                        // Explicit cancel via answer=false
                        if (!required) {
                            sendBooleanOrDie(gameId, false, "chooseAction:GAME_TARGET_cancel");
                            result.action_taken = "cancelled";
                            break;
                        }
                        // Required target — can't cancel, fall through to auto-select
                        logger.warn("[" + client.getUsername() + "] choose_action: answer=false invalid for required GAME_TARGET, auto-selecting");
                    } else if (!required) {
                        // No index, no answer=false — return error for optional targets
                        return chooseActionDone(buildError(result, "missing_param",
                            "GAME_TARGET requires choice=pN to select a target, or choice=\"no\" to cancel targeting. "
                            + "Call get_action_choices first to see available targets.", true, action, true));
                    }

                    // Auto-select for required targets when index was invalid/missing
                    Set<UUID> autoTargets = findValidTargets(targetMsg);
                    if (autoTargets != null && !autoTargets.isEmpty()) {
                        UUID firstTarget = selectDeterministicTarget(autoTargets, processorState.decisionState().lastChoices());
                        logger.warn("[" + client.getUsername() + "] choose_action: auto-selecting first target for required GAME_TARGET");
                        sendUuidOrDie(gameId, firstTarget, "chooseAction:GAME_TARGET_auto_select");
                        result.action_taken = "auto_selected_required_target";
                        result.warning = "Required target auto-selected. Use get_action_choices first, then index=N.";
                    } else {
                        logger.error("[" + client.getUsername() + "] Required GAME_TARGET has no valid targets — cancelling to avoid infinite loop");
                        sendBooleanOrDie(gameId, false, "chooseAction:GAME_TARGET_no_valid");
                        result.action_taken = "cancelled_no_valid_targets";
                    }
                    break;
                }

                case GAME_CHOOSE_ABILITY: {
                    if (resolvedIndex == null) {
                        return chooseActionDone(buildError(result, "missing_param",
                            "GAME_CHOOSE_ABILITY requires index=N. Call get_action_choices first to see "
                            + "the available abilities, then choose_action with the index of the one you want.",
                            true, action, true));
                    }
                    List<Object> abilityChoices = processorState.decisionState().lastChoices();
                    if (abilityChoices == null || resolvedIndex < 0 || resolvedIndex >= abilityChoices.size()) {
                        logChoiceOutOfRangeDiagnostic(method, resolvedIndex, abilityChoices);
                        return chooseActionDone(buildError(result, "index_out_of_range",
                            "Index " + resolvedIndex + " is out of range"
                            + (abilityChoices != null ? " (valid: 0-" + (abilityChoices.size() - 1) + ")" : " (no choices loaded — call get_action_choices first)")
                            + ". Call get_action_choices to see current options.", true, action, true));
                    }
                    UUID abilityUUID = (UUID) abilityChoices.get(resolvedIndex);
                    sendUuidOrDie(gameId, abilityUUID, "chooseAction:GAME_CHOOSE_ABILITY");
                    result.action_taken = "selected_ability_" + resolvedIndex;
                    break;
                }

                case GAME_CHOOSE_CHOICE: {
                    // Support text parameter for choosing by name (e.g. creature type not in filtered list)
                    if (text != null && !text.isEmpty()) {
                        GameClientMessage choiceMsg = (GameClientMessage) data;
                        Choice choiceObj = choiceMsg.getChoice();
                        if (choiceObj == null) {
                            return chooseActionDone(buildError(result, "internal_error", "No choice available", false, action));
                        }
                        // Validate text is a legal choice
                        if (choiceObj.isKeyChoice()) {
                            // For key choices, text must match a value; find the key
                            Map<String, String> keyChoices = choiceObj.getKeyChoices();
                            String matchedKey = null;
                            if (keyChoices != null) {
                                for (Map.Entry<String, String> entry : keyChoices.entrySet()) {
                                    if (entry.getValue().equalsIgnoreCase(text) || entry.getKey().equalsIgnoreCase(text)) {
                                        matchedKey = entry.getKey();
                                        break;
                                    }
                                }
                            }
                            if (matchedKey == null) {
                                return chooseActionDone(buildError(result, "invalid_choice",
                                    "'" + text + "' is not a valid choice", true, action, true));
                            }
                            sendStringOrDie(gameId, matchedKey, "chooseAction:GAME_CHOOSE_CHOICE_key");
                        } else {
                            // For plain choices, text must match a choice string
                            Set<String> choices = choiceObj.getChoices();
                            String matched = null;
                            if (choices != null) {
                                for (String c : choices) {
                                    if (c.equalsIgnoreCase(text)) {
                                        matched = c;
                                        break;
                                    }
                                }
                            }
                            if (matched == null) {
                                return chooseActionDone(buildError(result, "invalid_choice",
                                    "'" + text + "' is not a valid choice", true, action, true));
                            }
                            sendStringOrDie(gameId, matched, "chooseAction:GAME_CHOOSE_CHOICE");
                        }
                        result.action_taken = "selected_choice_text_" + text;
                        break;
                    }
                    if (id != null && !id.isEmpty()) {
                        return chooseActionDone(buildError(result, "invalid_choice",
                            "GAME_CHOOSE_CHOICE does not accept choice=\"" + id + "\" by name. "
                            + "Use text=\"" + id + "\" or choice=N with the current options.",
                            true, action, true));
                    }
                    if (resolvedIndex == null) {
                        return chooseActionDone(buildError(result, "missing_param",
                            "Integer 'index' or string 'text' required for GAME_CHOOSE_CHOICE", true, action, true));
                    }
                    List<Object> choiceChoices = processorState.decisionState().lastChoices();
                    if (choiceChoices == null || resolvedIndex < 0 || resolvedIndex >= choiceChoices.size()) {
                        logChoiceOutOfRangeDiagnostic(method, resolvedIndex, choiceChoices);
                        return chooseActionDone(buildError(result, "index_out_of_range",
                            "Index " + resolvedIndex + " is out of range"
                            + (choiceChoices != null ? " (valid: 0-" + (choiceChoices.size() - 1) + ")" : " (no choices loaded — call get_action_choices first)")
                            + ". Call get_action_choices to see current options.", true, action, true));
                    }
                    String choiceStr = (String) choiceChoices.get(resolvedIndex);
                    sendStringOrDie(gameId, choiceStr, "chooseAction:GAME_CHOOSE_CHOICE_index");
                    result.action_taken = "selected_choice_" + resolvedIndex;
                    break;
                }

                case GAME_CHOOSE_PILE:
                    if (pile == null) {
                        return chooseActionDone(buildError(result, "missing_param",
                            "Integer 'pile' (1 or 2) required for GAME_CHOOSE_PILE", true, action));
                    }
                    boolean pileChoice = pile == 1;
                    sendBooleanOrDie(gameId, pileChoice, "chooseAction:GAME_CHOOSE_PILE");
                    result.action_taken = "selected_pile_" + pile;
                    break;

                case GAME_GET_AMOUNT: {
                    if (amount == null) {
                        return chooseActionDone(buildError(result, "missing_param",
                            "Integer 'amount' required for GAME_GET_AMOUNT", true, action));
                    }
                    GameClientMessage msg = (GameClientMessage) data;
                    int clamped = Math.max(msg.getMin(), Math.min(msg.getMax(), amount));
                    sendIntegerOrDie(gameId, clamped, "chooseAction:GAME_GET_AMOUNT");
                    result.action_taken = "amount_" + clamped;
                    break;
                }

                case GAME_GET_MULTI_AMOUNT: {
                    if (amounts == null) {
                        return chooseActionDone(buildError(result, "missing_param",
                            "Array 'amounts' required for GAME_GET_MULTI_AMOUNT", true, action));
                    }
                    GameClientMessage msg = (GameClientMessage) data;
                    String validationError;
                    try {
                        validationError = validateMultiAmountInput(msg, amounts);
                    } catch (IllegalStateException e) {
                        return chooseActionDone(buildError(result, "internal_error", e.getMessage(), false, action));
                    }
                    if (validationError != null) {
                        return chooseActionDone(buildError(result, "invalid_multi_amount", validationError, true, action));
                    }
                    var sb = new StringBuilder();
                    for (int i = 0; i < amounts.length; i++) {
                        if (i > 0) sb.append(" ");
                        sb.append(amounts[i]);
                    }
                    String multiAmountStr = sb.toString();
                    sendStringOrDie(gameId, multiAmountStr, "chooseAction:GAME_GET_MULTI_AMOUNT");
                    result.action_taken = "multi_amount";
                    break;
                }

                default:
                    return chooseActionDone(buildError(result, "unknown_action_type", "Unknown action type: " + method, false, null));
            }
        } catch (ResponseDeliveryException e) {
            result.success = false;
            result.error = e.getMessage();
            result.error_code = "response_delivery_failed";
            result.retryable = false;
            attachUnseenChat(result);
            return chooseActionDone(result);
        } finally {
            processorState.decisionState().clearLastChoices();
            if (Boolean.FALSE.equals(result.success)) {
                logger.warn("[" + client.getUsername() + "] choose_action failed: " + result.error);
            }
        }

        return chooseActionAwaitNextDecision(result);
    }

    private ChooseActionTool.Result chooseActionDeliveryErrorResult(String message) {
        var result = new ChooseActionTool.Result();
        result.success = false;
        result.error = message;
        result.error_code = "response_delivery_failed";
        result.retryable = false;
        attachUnseenChat(result);
        return result;
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

    /**
     * Inspect the current pending action and auto-resolve any deterministic
     * non-decision callbacks without waiting for a future callback.
     */
    PendingAction currentDecisionAction() {
        while (true) {
            PendingAction action = processorState.decisionState().pendingAction();
            if (action == null) {
                return null;
            }
            DecisionBoundaryTransition transition =
                transitionToDecisionBoundary(action, "currentDecisionAction");
            if (transition.status() == DecisionBoundaryStatus.READY) {
                return transition.action();
            }
        }
    }

    private String safeDisplayName(CardView cv) {
        return cardFormatter.safeDisplayName(cv);
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
     * Drain unseen chat messages and attach to result map (if any).
     */
    private void attachUnseenChat(Map<String, Object> result) {
        processorState.gameLogState().attachUnseenChat(result, processorState.gameState().playerDead(), processorState.gameState().gameOverObserved());
    }

    private void attachUnseenChat(ActionResult result) {
        processorState.gameLogState().attachUnseenChat(result, processorState.gameState().playerDead(), processorState.gameState().gameOverObserved());
    }

    private void mergeActionChoices(ActionResult result, Long boardCursorParam, PendingAction action) {
        ActionResult choices = buildActionChoices(action, boardCursorParam);
        if (!Boolean.TRUE.equals(choices.action_pending)) {
            // The caller expected to merge a specific observed action, but by merge time
            // there was no longer a stable decision to expose.
            result.warning = "Action changed before choices were fetched";
            return;
        }
        // Merge all choice fields into the result.  pass_priority fields
        // (action_pending, stop_reason, etc.) are already set
        // and take precedence — only copy fields the result doesn't have yet.
        result.mergeFrom(choices);
    }

    ActionResult pendingActionResult(
            PendingAction action,
            String stopReason,
            Long boardCursorParam
    ) {
        return pendingActionResult(action, stopReason, boardCursorParam, null);
    }

    ActionResult pendingActionResult(
            PendingAction action,
            String stopReason,
            Long boardCursorParam,
            Consumer<ActionResult> customizer
    ) {
        var result = new ActionResult();
        result.action_pending = true;
        result.action_type = action.method().name();
        result.game_seq = action.gameSeq();
        result.stop_reason = stopReason;
        if (customizer != null) {
            customizer.accept(result);
        }
        attachUnseenChat(result);
        mergeActionChoices(result, boardCursorParam, action);
        return result;
    }

    ActionResult stackResolvedResult(PendingAction action, Long boardCursorParam) {
        return pendingActionResult(action, "stack_resolved", boardCursorParam);
    }

    ActionResult stepYieldResult(PendingAction action, GameView gv, String stopReason, Long boardCursorParam) {
        return pendingActionResult(action, stopReason, boardCursorParam, result -> {
            if (gv != null && gv.getStep() != null) {
                result.current_step = gv.getStep().toString();
            }
        });
    }

    UUID lowestStackObjectId(GameView gameView) {
        if (gameView == null || gameView.getStack() == null || gameView.getStack().isEmpty()) {
            return null;
        }
        // SpellStack iterates top-first and CardsView preserves insertion order,
        // so the last key is the lowest stack object present when the yield starts.
        UUID lowest = null;
        for (UUID stackObjectId : gameView.getStack().keySet()) {
            lowest = stackObjectId;
        }
        return lowest;
    }

    boolean stackContains(GameView gameView, UUID stackObjectId) {
        return gameView != null
            && gameView.getStack() != null
            && stackObjectId != null
            && gameView.getStack().containsKey(stackObjectId);
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

    private int getStableShortIdSequence(UUID objectId) {
        return viewLocator.getStableShortIdSequence(objectId);
    }

    private int getStableShortIdSequence(UUID objectId, CardView cardView) {
        return viewLocator.getStableShortIdSequence(objectId, cardView);
    }

    private CardView findCardViewById(UUID objectId, GameView gameView) {
        return viewLocator.findCardViewById(objectId, gameView);
    }

    /**
     * Check if a pending GAME_SELECT is a combat selection (declare attackers or blockers)
     * by inspecting the options map for possibleAttackers/possibleBlockers keys.
     * Returns "attackers", "blockers", or null.
     */
    String detectCombatSelect(PendingAction action) {
        if (action == null || action.method() != ClientCallbackMethod.GAME_SELECT) {
            return null;
        }
        Object data = action.data();
        if (data instanceof GameClientMessage gcm) {
            Map<String, Serializable> options = gcm.getOptions();
            if (options != null) {
                if (options.containsKey("possibleAttackers")) {
                    return "attackers";
                }
                if (options.containsKey("possibleBlockers")) {
                    return "blockers";
                }
            }
        }
        return null;
    }

    public void handleCallback(ClientCallback callback) {
        callbackIngress.handleCallback(callback);
    }

    static String stripAbilityPickerOrdinalPrefix(String description, int zeroBasedIndex) {
        return BridgePromptFormatting.stripAbilityPickerOrdinalPrefix(description, zeroBasedIndex);
    }

    /**
     * Find valid targets from multiple sources in a GameClientMessage.
     * This handles both standard targeting (message.getTargets()) and
     * card-from-zone selection (options.possibleTargets or cardsView1).
     */
    @SuppressWarnings("unchecked")
    private Set<UUID> findValidTargets(GameClientMessage message) {
        // 1. Try message.getTargets() first (standard targeting)
        Set<UUID> targets = message.getTargets();
        if (targets != null && !targets.isEmpty()) {
            return targets;
        }

        // 2. Try options.get("possibleTargets") (card-from-zone selection)
        Map<String, Serializable> options = message.getOptions();
        if (options != null) {
            Object possibleTargets = options.get("possibleTargets");
            if (possibleTargets instanceof Set<?> possibleSet) {
                @SuppressWarnings("unchecked")
                Set<UUID> possible = (Set<UUID>) possibleSet;
                if (!possible.isEmpty()) {
                    return possible;
                }
            }
        }

        // 3. Fall back to cardsView1.keySet() (cards displayed for selection)
        CardsView cardsView = message.getCardsView1();
        if (cardsView != null && !cardsView.isEmpty()) {
            return cardsView.keySet();
        }

        return null;
    }

    private UUID selectSingleRequiredTarget(GameClientMessage message) {
        if (message == null || !message.isFlag()) {
            return null;
        }
        Set<UUID> targets = findValidTargets(message);
        if (targets == null || targets.size() != 1) {
            return null;
        }
        return selectDeterministicTarget(targets, null);
    }

    /**
     * Select a deterministic target from a set of valid targets.
     * Prefer the order from choices (if provided), otherwise fall back to
     * short ID sequence ordering (stable across runs, unlike UUID).
     */
    private UUID selectDeterministicTarget(Set<UUID> targets, List<Object> choices) {
        if (targets == null || targets.isEmpty()) {
            return null;
        }

        if (choices != null && !choices.isEmpty()) {
            for (Object choice : choices) {
                if (choice instanceof UUID candidate && targets.contains(candidate)) {
                    return candidate;
                }
            }
        }

        UUID selected = null;
        int selectedSeq = Integer.MAX_VALUE;
        for (UUID candidate : targets) {
            // Use getStableShortIdSequence to ensure server-assigned IDs are
            // registered from the current GameView before comparing sequences.
            // Without this, getSequence returns MAX_VALUE for unregistered UUIDs,
            // causing the selection to depend on HashSet iteration order.
            int seq = getStableShortIdSequence(candidate);
            if (selected == null || seq < selectedSeq) {
                selected = candidate;
                selectedSeq = seq;
            }
        }
        return selected;
    }

    private UUID extractPayingForId(String message) {
        // Extract object_id='...' from callback HTML so we can avoid tapping the paid object itself.
        if (message == null) {
            return null;
        }
        int idx = message.indexOf("object_id='");
        if (idx < 0) {
            return null;
        }
        int start = idx + "object_id='".length();
        int end = message.indexOf("'", start);
        if (end <= start) {
            return null;
        }
        try {
            return UUID.fromString(message.substring(start, end));
        } catch (IllegalArgumentException e) {
            return null;
        }
    }

    private ManaPoolView getMyManaPoolView(GameView gameView) {
        if (gameView == null) {
            return null;
        }
        PlayerView myPlayer = gameView.getMyPlayer();
        if (myPlayer == null) {
            return null;
        }
        return myPlayer.getManaPool();
    }

    private int getManaPoolCount(ManaPoolView manaPool, ManaType manaType) {
        if (manaPool == null) {
            return 0;
        }
        return switch (manaType) {
            case WHITE -> manaPool.getWhite();
            case BLUE -> manaPool.getBlue();
            case BLACK -> manaPool.getBlack();
            case RED -> manaPool.getRed();
            case GREEN -> manaPool.getGreen();
            case COLORLESS -> manaPool.getColorless();
            case GENERIC -> 0;
        };
    }

    private void addPreferredPoolManaChoice(List<ManaType> orderedChoices, ManaPoolView manaPool, ManaType manaType) {
        if (getManaPoolCount(manaPool, manaType) > 0 && !orderedChoices.contains(manaType)) {
            orderedChoices.add(manaType);
        }
    }

    private boolean hasExplicitManaSymbol(String promptText) {
        if (promptText == null) {
            return false;
        }
        return REGEX_WHITE.matcher(promptText).find()
                || REGEX_BLUE.matcher(promptText).find()
                || REGEX_BLACK.matcher(promptText).find()
                || REGEX_RED.matcher(promptText).find()
                || REGEX_GREEN.matcher(promptText).find()
                || REGEX_COLORLESS.matcher(promptText).find();
    }

    private boolean addExplicitPoolChoices(List<ManaType> orderedChoices, ManaPoolView manaPool, String promptText) {
        if (promptText == null) {
            return false;
        }
        boolean hasExplicitSymbols = false;
        if (REGEX_WHITE.matcher(promptText).find()) {
            hasExplicitSymbols = true;
            addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.WHITE);
        }
        if (REGEX_BLUE.matcher(promptText).find()) {
            hasExplicitSymbols = true;
            addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.BLUE);
        }
        if (REGEX_BLACK.matcher(promptText).find()) {
            hasExplicitSymbols = true;
            addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.BLACK);
        }
        if (REGEX_RED.matcher(promptText).find()) {
            hasExplicitSymbols = true;
            addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.RED);
        }
        if (REGEX_GREEN.matcher(promptText).find()) {
            hasExplicitSymbols = true;
            addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.GREEN);
        }
        if (REGEX_COLORLESS.matcher(promptText).find()) {
            hasExplicitSymbols = true;
            addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.COLORLESS);
        }
        return hasExplicitSymbols;
    }

    private List<ManaType> getPoolManaChoices(GameView gameView, String promptText) {
        ManaPoolView manaPool = getMyManaPoolView(gameView);
        if (manaPool == null) {
            return new ArrayList<>();
        }

        var orderedChoices = new ArrayList<ManaType>();
        boolean hasExplicitSymbols = addExplicitPoolChoices(orderedChoices, manaPool, promptText);
        if (hasExplicitSymbols) {
            // If explicit symbols are present (e.g. "{G}"), only offer matching pool mana types.
            return orderedChoices;
        }

        // Generic/no-symbol payment: allow any available pool mana in stable order.
        addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.WHITE);
        addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.BLUE);
        addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.BLACK);
        addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.RED);
        addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.GREEN);
        addPreferredPoolManaChoice(orderedChoices, manaPool, ManaType.COLORLESS);

        return orderedChoices;
    }

    /**
     * Parse a mana plan String[] into a list of ManaPlanEntry.
     * Format: ["p1", "p2:0", "RED"] — short IDs activate mana abilities (with optional
     * :N ability index for multi-ability permanents), color names spend from pool.
     */
    private CopyOnWriteArrayList<BridgeManaPlanEntry> parseManaPlan(String[] arr) {
        var plan = new CopyOnWriteArrayList<BridgeManaPlanEntry>();
        for (String entry : arr) {
            if (isPoolColor(entry)) {
                plan.add(new BridgeManaPlanEntry("pool", entry));
            } else {
                int colonIdx = entry.indexOf(':');
                if (colonIdx >= 0) {
                    String shortId = entry.substring(0, colonIdx);
                    int abilityIndex = Integer.parseInt(entry.substring(colonIdx + 1));
                    plan.add(new BridgeManaPlanEntry("tap", shortId, abilityIndex));
                } else {
                    plan.add(new BridgeManaPlanEntry("tap", entry));
                }
            }
        }
        return plan;
    }

    private static boolean isPoolColor(String s) {
        try { ManaType.valueOf(s); return true; }
        catch (IllegalArgumentException e) { return false; }
    }

    /**
     * Cancel a spell because the mana plan was incorrect (entry failed or plan exhausted).
     * Marks the spell as failed, clears the plan, and notifies the LLM.
     */
    private boolean cancelSpellFromBadManaPlan(UUID gameId, UUID payingForId) {
        processorState.interactionState().markFailedManaCast(payingForId);
        processorState.interactionState().clearManaPlan();
        processorState.gameLogState().addSystemMessage("[System] Spell cancelled — mana plan was incorrect or incomplete.");
        logBridgeEvent("SPELL_CANCELLED", "mana plan was incorrect or incomplete");
        sendBooleanOrDie(gameId, false, "cancelSpellFromBadManaPlan");
        return true;
    }

    private UUID getManaPoolPlayerId(UUID gameId, GameView gameView) {
        if (gameView != null) {
            PlayerView myPlayer = gameView.getMyPlayer();
            if (myPlayer != null && myPlayer.getPlayerId() != null) {
                return myPlayer.getPlayerId();
            }
        }
        return playerIdForGame(gameId);
    }

    /**
     * Try to auto-tap a mana source. Returns true if a source was tapped,
     * false if no suitable source was found (caller should fall through to LLM).
     */
    private boolean handleGamePlayManaAuto(UUID gameId, GameClientMessage message) {
        GameView gameView = message.getGameView();
        updateLastGameView(gameView, "GAME_PLAY_MANA_AUTO");

        String msg = message.getMessage();
        UUID payingForId = extractPayingForId(msg);

        // Consume explicit mana plan if active.
        // If any entry fails or the plan is exhausted, cancel the spell — the LLM
        // must either pass a CORRECT plan, fill the pool in advance, or use auto_tap.
        CopyOnWriteArrayList<BridgeManaPlanEntry> plan = processorState.interactionState().manaPlan();
        if (plan != null && !plan.isEmpty()) {
            BridgeManaPlanEntry entry = plan.remove(0);  // consume first entry

            if ("tap".equals(entry.type())) {
                processorState.interactionState().setManaPlanAbilityIndex(entry.abilityIndex());  // save for GAME_CHOOSE_ABILITY
                UUID targetId = shortIds.tryResolve(entry.value());
                if (targetId == null) {
                    logger.warn("[" + client.getUsername() + "] Mana plan: unknown short ID '" + entry.value() + "', cancelling spell");
                    return cancelSpellFromBadManaPlan(gameId, payingForId);
                }
                PlayableObjectsList playableForPlan = gameView != null ? gameView.getCanPlayObjects() : null;
                if (playableForPlan != null) {
                    PlayableObjectStats stats = playableForPlan.getObjects().get(targetId);
                    if (stats != null && !targetId.equals(payingForId) && !processorState.interactionState().failedManaCast(targetId)) {
                        logger.info("[" + client.getUsername() + "] Mana plan: \"" + msg + "\" -> tapping " + entry.value());
                        processorState.interactionState().resetPoolManaTracking();
                        sendUuidOrDie(gameId, targetId, "manaAuto:plan_tap");
                        return true;
                    }
                }
                // ID not found/not available — cancel spell
                logger.warn("[" + client.getUsername() + "] Mana plan: tap target " + entry.value() + " not available, cancelling spell");
                return cancelSpellFromBadManaPlan(gameId, payingForId);
            }

            if ("pool".equals(entry.type())) {
                ManaType manaType = ManaType.valueOf(entry.value());
                UUID manaPlayerId = getManaPoolPlayerId(gameId, gameView);
                if (manaPlayerId != null) {
                    logger.info("[" + client.getUsername() + "] Mana plan: \"" + msg + "\" -> using pool " + manaType);
                    sendManaTypeOrDie(gameId, manaPlayerId, manaType, "manaAuto:plan_pool");
                    return true;
                }
                logger.warn("[" + client.getUsername() + "] Mana plan: pool entry failed (no player ID), cancelling spell");
                return cancelSpellFromBadManaPlan(gameId, payingForId);
            }

            // Unknown entry type — cancel spell
            logger.warn("[" + client.getUsername() + "] Mana plan: unknown entry type '" + entry.type() + "', cancelling spell");
            return cancelSpellFromBadManaPlan(gameId, payingForId);
        }

        // Plan exists but is exhausted — either fall through to auto-tap or cancel
        if (plan != null) {
            if (processorState.interactionState().manaPlanAutoTapFallback()) {
                logger.info("[" + client.getUsername() + "] Mana plan: exhausted, falling through to auto-tap for remaining pips");
                processorState.interactionState().clearManaPlan();
                // Fall through to auto-tap code below
            } else {
                logger.warn("[" + client.getUsername() + "] Mana plan: exhausted with pips remaining, cancelling spell (auto_tap=false)");
                return cancelSpellFromBadManaPlan(gameId, payingForId);
            }
        }

        // Find a mana source from canPlayObjects and tap it
        PlayableObjectsList playable = gameView != null ? gameView.getCanPlayObjects() : null;
        if (playable != null && !playable.isEmpty()) {
            // Build a deterministic ordering for mana sources.
            // Prefer battlefield insertion order to avoid HashMap iteration nondeterminism.
            var battlefieldOrder = new HashMap<UUID, Integer>();
            if (gameView != null) {
                int order = 0;
                for (PlayerView player : gameView.getPlayers()) {
                    for (UUID permanentId : player.getBattlefield().keySet()) {
                        battlefieldOrder.put(permanentId, order++);
                    }
                }
            }
            var sortedPlayable = new ArrayList<>(playable.getObjects().entrySet());
            sortedPlayable.sort(Comparator.<Map.Entry<UUID, PlayableObjectStats>>comparingInt(e -> {
                Integer idx = battlefieldOrder.get(e.getKey());
                return idx != null ? idx : Integer.MAX_VALUE;
            }).thenComparing(e -> {
                CardView cv = findCardViewById(e.getKey(), gameView);
                return cv != null ? safeDisplayName(cv) : "";
            }).thenComparingInt(e -> getStableShortIdSequence(e.getKey(), findCardViewById(e.getKey(), gameView))));

            // Find the first object that has a mana ability (but skip the object being paid for)
            for (Map.Entry<UUID, PlayableObjectStats> entry : sortedPlayable) {
                UUID objectId = entry.getKey();
                // Don't tap the source we're paying for — it may need {T}/sacrifice as part of its cost
                if (objectId.equals(payingForId)) {
                    continue;
                }
                // Don't re-tap a source whose activation cost already failed to pay
                if (processorState.interactionState().failedManaCast(objectId)) {
                    continue;
                }
                PlayableObjectStats stats = entry.getValue();
                // Only auto-tap mana abilities that use {T} with no additional mana cost.
                // Non-tap mana abilities (sacrifice, discard, etc.) have strategic cost.
                // Abilities like "{1}, {T}: Add {B}{R}" (Shadowblood Ridge) cost mana to
                // activate — tapping them triggers a sub-payment that can loop infinitely.
                boolean hasTapManaAbility = false;
                for (String name : stats.getAllManaAbilityNames()) {
                    if (name.contains("{T}")) {
                        // Check that the activation cost (before ':') doesn't require mana
                        int colonPos = name.indexOf(':');
                        if (colonPos > 0) {
                            String costPart = name.substring(0, colonPos);
                            if (costPart.matches(".*\\{[0-9WUBRGC]\\}.*")) {
                                continue; // Non-free activation cost — skip
                            }
                        }
                        hasTapManaAbility = true;
                        break;
                    }
                }
                if (hasTapManaAbility) {
                    logger.info("[" + client.getUsername() + "] Mana: \"" + msg + "\" -> tapping " + objectId.toString().substring(0, 8));
                    processorState.interactionState().resetPoolManaTracking(); // Reset pool counter — tap may produce needed mana
                    sendUuidOrDie(gameId, objectId, "manaAuto:tap");
                    return true;
                }
            }
        }

        // Try to spend mana already in pool.
        List<ManaType> poolChoices = getPoolManaChoices(gameView, msg);
        if (!poolChoices.isEmpty()) {
            UUID manaPlayerId = getManaPoolPlayerId(gameId, gameView);
            boolean canAutoSelectPoolType = poolChoices.size() == 1 || hasExplicitManaSymbol(msg);
            if (manaPlayerId != null) {
                // Track consecutive pool payment attempts for the same spell.
                // If XMage keeps re-sending GAME_PLAY_MANA after we send pool mana,
                // the payment isn't actually progressing — cancel to break the loop.
                int poolManaAttempts = processorState.interactionState().recordPoolManaAttempt(payingForId);
                if (poolManaAttempts > MAX_POOL_MANA_ATTEMPTS) {
                    logger.warn("[" + client.getUsername() + "] Mana: \"" + msg + "\" -> pool payment not progressing after "
                            + poolManaAttempts + " attempts, cancelling spell");
                    processorState.interactionState().resetPoolManaTracking();
                    processorState.interactionState().clearManaPlan();
                    processorState.interactionState().markFailedManaCast(payingForId);
                    processorState.gameLogState().addSystemMessage("[System] Spell cancelled — not enough mana to complete payment.");
                    logBridgeEvent("SPELL_CANCELLED", "not enough mana to complete payment");
                    sendBooleanOrDie(gameId, false, "manaAuto:pool_loop_cancel");
                    return true;
                }

                if (!canAutoSelectPoolType) {
                    logger.info("[" + client.getUsername() + "] Mana: \"" + msg + "\" -> pool has multiple options, waiting for manual choice");
                    return false;
                }
                ManaType manaType = poolChoices.get(0);
                logger.info("[" + client.getUsername() + "] Mana: \"" + msg + "\" -> using pool " + manaType.toString());
                sendManaTypeOrDie(gameId, manaPlayerId, manaType, "manaAuto:pool");
                return true;
            }
            logger.warn("[" + client.getUsername() + "] Mana: couldn't resolve player ID for mana pool payment");
        }

        // No suitable source/pool choice found — cancel spell and mark as failed.
        logger.info("[" + client.getUsername() + "] Mana: \"" + msg + "\" -> no mana source available, cancelling spell");
        processorState.interactionState().markFailedManaCast(payingForId);
        processorState.interactionState().clearManaPlan();
        processorState.gameLogState().addSystemMessage("[System] Spell cancelled — not enough mana to complete payment.");
        logBridgeEvent("SPELL_CANCELLED", "not enough mana to complete payment");
        sendBooleanOrDie(gameId, false, "manaAuto:no_source_cancel");
        return true;
    }

}
