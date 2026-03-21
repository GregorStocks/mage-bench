package mage.client.bridge;

import mage.client.bridge.processor.BridgeActionableCallbackOutcome;
import mage.client.bridge.processor.BridgeCallbackDispatcher;
import mage.client.bridge.processor.BridgeCallbackDispatcherContext;
import mage.client.bridge.processor.BridgeCallbackEvent;
import mage.client.bridge.processor.BridgeChooseActionFlow;
import mage.client.bridge.processor.BridgeChooseActionFlowContext;
import mage.client.bridge.processor.BridgeChooseActionInput;
import mage.client.bridge.processor.BridgeChooseActionStartResult;
import mage.client.bridge.processor.BridgeCommand;
import mage.client.bridge.processor.BridgePassPriorityFlow;
import mage.client.bridge.processor.BridgePassPriorityFlowContext;
import mage.client.bridge.processor.BridgeProcessor;
import mage.cards.decks.DeckCardInfo;
import mage.cards.decks.DeckCardLists;
import mage.game.BridgeLogEntry;
import mage.cards.repository.CardInfo;
import mage.cards.repository.CardRepository;
import mage.choices.Choice;
import mage.constants.ManaType;
import mage.constants.PhaseStep;
import mage.constants.PlayerAction;
import mage.constants.SubType;
import mage.constants.SubTypeSet;
import mage.interfaces.callback.ClientCallback;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.remote.Session;
import mage.view.AbilityPickerView;
import mage.view.CardsView;
import mage.view.CardView;
import mage.view.ChatMessage;
import mage.view.CombatGroupView;
import mage.view.GameClientMessage;
import mage.view.GameView;
import mage.view.ManaPoolView;
import mage.view.PermanentView;
import mage.view.PlayerView;
import mage.view.TableClientMessage;
import mage.view.UserRequestMessage;
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
import mage.client.bridge.tools.McpToolRegistry;

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
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeoutException;
import java.util.function.Consumer;
import java.util.regex.Pattern;

/**
 * Callback handler for the bridge client.
 * Stores pending actions for external clients to handle via MCP.
 * Higher-level controller roles such as
 * pilot, replay, and the Python-side sleepwalker live above this layer.
 */
public class BridgeCallbackHandler {

    private static final Logger logger = Logger.getLogger(BridgeCallbackHandler.class);
    /** Snapshot of the cached bridge-event log plus the next cursor to hand back to callers. */
    private record GameLogSnapshot(List<BridgeLogEntry> events, int cursor) {}

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
    private final BridgeChooseActionFlowContext chooseActionFlowContext;
    private final BridgePassPriorityFlowContext passPriorityFlowContext;
    // Step 1/2 processor scaffold: callback ingress now goes through the processor thread,
    // while MCP methods still read transitional shared fields until step 3 lands.
    private final BridgeProcessor processor;
    private volatile Session session;
    private final Map<UUID, UUID> activeGames = new ConcurrentHashMap<>(); // gameId -> playerId
    private final Map<UUID, UUID> gameChatIds = new ConcurrentHashMap<>(); // gameId -> chatId

    private volatile boolean keepAliveAfterGame = false;
    private volatile boolean gameEverStarted = false;
    private volatile PendingAction pendingAction = null;
    private BridgeChooseActionFlow pendingChooseActionFlow = null;
    private BridgePassPriorityFlow pendingPassPriorityFlow = null;
    private final Object actionLock = new Object(); // For wait_for_action blocking
    private volatile UUID currentGameId = null;
    private volatile UUID currentPlayerId = null; // retained after GAME_OVER for postgame fetches
    private volatile UUID expectedStartTableId = null; // keepAlive join_table guard
    private volatile boolean startGameArmed = false; // keepAlive join_table must arm the next START_GAME
    private volatile boolean superseded = false; // set when createFreshForNextGame() replaces this handler
    private volatile GameView lastGameView = null;
    private final RoundTracker roundTracker = new RoundTracker();

    /** Update lastGameView with source tracking for determinism debugging.
     *  Synchronized to prevent TOCTOU race: two threads reading the same old value,
     *  both passing the monotonic guard, and the lower-seq thread writing last. */
    private synchronized void updateLastGameView(GameView gv, String source) {
        if (gv != null) {
            GameView old = lastGameView;
            if (old != null && gv.getGameSeq() < old.getGameSeq()) {
                String src = source != null ? source : "unknown";
                logger.warn("[" + client.getUsername() + "] lastGameView REJECTED backward update game_seq "
                    + old.getGameSeq() + " -> " + gv.getGameSeq() + " (source=" + src
                    + ", thread=" + Thread.currentThread().getName() + ")");
                return;
            }
            lastGameView = gv;
            roundTracker.update(gv);
            // Determinism debugging: log when game_seq changes and who changed it
            int oldSeq = old != null ? old.getGameSeq() : -1;
            int newSeq = gv.getGameSeq();
            if (oldSeq != newSeq) {
                String src = source != null ? source : "unknown";
                String step = gv.getStep() != null ? gv.getStep().toString() : "null";
                logger.debug("[" + client.getUsername() + "] lastGameView game_seq " + oldSeq
                    + " -> " + newSeq + " (source=" + src + ", step=" + step
                    + ", thread=" + Thread.currentThread().getName() + ")");
            }
        }
    }


    private final ShortIdRegistry shortIds = new ShortIdRegistry("l");
    private volatile List<Object> lastChoices = null; // Index→UUID/String mapping for choose_action
    private volatile String lastChoicesActionType = null; // Debug context for stale-choice diagnostics
    private volatile String lastChoicesResponseType = null; // Debug context for stale-choice diagnostics
    private volatile int lastChoicesCount = -1; // Debug context for stale-choice diagnostics
    private volatile long lastChoicesGeneratedAtMs = 0; // Debug context for stale-choice diagnostics
    private final Object stateCursorLock = new Object();
    private volatile long gameStateCursor = 0; // Monotonic cursor for get_game_state
    private volatile String lastGameStateSignature = null; // Canonicalized state signature for cursoring
    private final Object boardCursorLock = new Object();
    private volatile long boardCursor = 0; // Monotonic cursor for board state dedup in pass_priority/get_action_choices
    private volatile String lastBoardSignature = null; // Canonicalized board signature for cursoring
    private final Set<UUID> failedManaCasts = ConcurrentHashMap.newKeySet(); // Spells that failed mana payment (avoid retry loops)
    private volatile UUID poolManaPayingForId = null; // Tracks which spell pool-mana is being paid for (loop detection)
    private volatile int poolManaAttempts = 0; // Consecutive pool-mana sends for the same spell
    private static final int MAX_POOL_MANA_ATTEMPTS = 10; // Cancel payment after this many pool retries
    private volatile CopyOnWriteArrayList<ManaPlanEntry> manaPlan = null; // Explicit mana sourcing plan from LLM
    private volatile Integer manaPlanAbilityIndex = null; // Ability index from last consumed mana plan entry (for GAME_CHOOSE_ABILITY)
    private volatile boolean manaPlanAutoTapFallback = true; // When mana plan is exhausted, fall through to auto-tap (true) or cancel (false)
    private volatile int lastTurnNumber = -1; // For clearing failedManaCasts on turn change
    private volatile int interactionsThisTurn = 0; // Generic loop detection: count model interactions per turn
    private volatile int maxInteractionsPerTurn = 25; // Configurable per-model; after this many, auto-pass rest of turn

    private volatile DeckCardLists deckList = null; // Original decklist for get_my_decklist
    private volatile String errorLogPath = null; // Path to write errors to (set via system property)
    private volatile String bridgeLogPath = null; // Path to write bridge JSONL dump
    private final List<String> unseenChat = new ArrayList<>(); // Chat messages from other players not yet shown to LLM
    private volatile boolean playerDead = false; // Set when we see "{name} has lost the game" in chat
    private final List<BridgeChatLogEntry> chatLog = new ArrayList<>(); // Chat messages interleaved with bridge events at render time
    private volatile String lastChatMessage = null; // For deduplicating outgoing chat
    private volatile long lastChatTimeMs = 0; // Timestamp of last outgoing chat
    private static final long CHAT_DEDUP_WINDOW_MS = 30_000; // Suppress identical messages within 30s
    private volatile int bridgeEventCursor = 0; // Pull cursor for bridge event log
    private final List<BridgeLogEntry> cachedBridgeEvents = new ArrayList<>(); // Client-side cache survives game cleanup
    private static final long KEEPALIVE_CONCEDE_WAIT_SECONDS = 15;

    // Keep-alive multi-game support: latches for cross-thread signaling
    private volatile CountDownLatch gameStartLatch = new CountDownLatch(1);
    private volatile CountDownLatch gameFinishedLatch = new CountDownLatch(1);

    // Join handler: provided by BridgeClient so JoinTableTool can trigger table joining
    @FunctionalInterface
    public interface JoinHandler {
        UUID joinTable(String deckPath, UUID targetTableId) throws Exception;
    }
    private volatile JoinHandler joinHandler = null;

    private record ManaPlanEntry(String type, String value, Integer abilityIndex) {
        ManaPlanEntry(String type, String value) { this(type, value, null); }
    }
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
    private volatile long lastCallbackReceivedAt = 0;
    // Track actionable callbacks (GAME_SELECT, GAME_ASK, etc.) separately from passive
    // ones (CHATMESSAGE, GAME_UPDATE). Used by zombie detection and progress logging.
    private static final EnumSet<ClientCallbackMethod> ACTIONABLE_CALLBACKS = EnumSet.of(
        ClientCallbackMethod.GAME_SELECT, ClientCallbackMethod.GAME_ASK,
        ClientCallbackMethod.GAME_TARGET, ClientCallbackMethod.GAME_CHOOSE_ABILITY,
        ClientCallbackMethod.GAME_CHOOSE_CHOICE, ClientCallbackMethod.GAME_CHOOSE_PILE,
        ClientCallbackMethod.GAME_PLAY_MANA, ClientCallbackMethod.GAME_PLAY_XMANA,
        ClientCallbackMethod.GAME_GET_AMOUNT, ClientCallbackMethod.GAME_GET_MULTI_AMOUNT);
    private volatile long lastActionableCallbackAt = 0;
    private static final ZoneId LOG_TZ = ZoneId.of("America/Los_Angeles");
    private static final DateTimeFormatter TIME_FMT =
        DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSSSSSXXX");

    public BridgeCallbackHandler(BridgeMageClient client) {
        this.client = client;
        this.viewLocator = new BridgeViewLocator(shortIds, () -> lastGameView, this::logError);
        this.cardFormatter = new BridgeCardFormatter(viewLocator, () -> currentGameId, this::playerIdForGame);
        this.gameStateBuilder = new BridgeGameStateBuilder(cardFormatter, viewLocator, () -> currentGameId, this::playerIdForGame);
        this.oracleTextService = new BridgeOracleTextService(shortIds, viewLocator);
        this.chooseActionFlowContext = createChooseActionFlowContext();
        this.passPriorityFlowContext = createPassPriorityFlowContext();
        BridgeCallbackDispatcher dispatcher = new BridgeCallbackDispatcher(new BridgeCallbackDispatcherContext() {
            @Override
            public String nonCurrentGameCallbackIgnoreReason(UUID callbackGameId, ClientCallbackMethod method) {
                return BridgeCallbackHandler.this.nonCurrentGameCallbackIgnoreReason(callbackGameId, method);
            }

            @Override
            public void logCallbackReceived(UUID callbackGameId, ClientCallbackMethod method, String ignoreReason) {
                BridgeCallbackHandler.this.logCallbackReceived(callbackGameId, method, ignoreReason);
            }

            @Override
            public boolean shouldIgnoreNonCurrentGameCallback(
                    UUID callbackGameId,
                    ClientCallbackMethod method,
                    String ignoreReason) {
                return BridgeCallbackHandler.this.shouldIgnoreNonCurrentGameCallback(callbackGameId, method, ignoreReason);
            }

            @Override
            public void recordCallbackArrival(ClientCallbackMethod method) {
                BridgeCallbackHandler.this.recordCallbackArrival(method);
            }

            @Override
            public BridgeActionableCallbackOutcome createActionableOutcome(ClientCallbackMethod method) {
                return new ActionableCallbackOutcome(method);
            }

            @Override
            public boolean shouldLogBridgeEvents() {
                return bridgeLogPath != null;
            }

            @Override
            public String buildBridgeStateSummary() {
                return BridgeCallbackHandler.this.buildBridgeStateSummary();
            }

            @Override
            public void logBridgeEvent(ClientCallbackMethod method, UUID gameId, String summary) {
                BridgeCallbackHandler.this.logBridgeEvent(method, gameId, summary);
            }

            @Override
            public void storePendingAction(UUID gameId, ClientCallbackMethod method, Object data) {
                BridgeCallbackHandler.this.storePendingAction(gameId, method, data);
            }

            @Override
            public void handleStartGame(UUID gameId, Object data) {
                BridgeCallbackHandler.this.handleStartGame(gameId, data);
            }

            @Override
            public void handleGameInit(Object data) {
                BridgeCallbackHandler.this.handleGameInit(data);
            }

            @Override
            public void logGameState(Object data) {
                BridgeCallbackHandler.this.logGameState(data);
            }

            @Override
            public void handleGameOver(UUID gameId, Object data) {
                BridgeCallbackHandler.this.handleGameOver(gameId, data);
            }

            @Override
            public void handleEndGameInfo(UUID gameId) {
                BridgeCallbackHandler.this.handleEndGameInfo(gameId);
            }

            @Override
            public void handleChatMessage(Object data) {
                BridgeCallbackHandler.this.handleChatMessage(data);
            }

            @Override
            public void logEvent(ClientCallbackMethod method, Object data) {
                BridgeCallbackHandler.this.logEvent(method, data);
            }

            @Override
            public void handleUserRequestDialog(Object data) {
                BridgeCallbackHandler.this.handleUserRequestDialog(data);
            }

            @Override
            public void logUnhandledCallback(ClientCallbackMethod method) {
                logger.debug("[" + client.getUsername() + "] Unhandled callback: " + method);
            }

            @Override
            public void handleProcessorCallbackException(
                    ClientCallbackMethod method,
                    Exception e,
                    boolean actionable) {
                BridgeCallbackHandler.this.handleCallbackException(method, e, actionable);
            }
        });
        this.processor = new BridgeProcessor(client.getUsername(), logger, dispatcher::process);
        this.processor.start();
    }

    private BridgeChooseActionFlowContext createChooseActionFlowContext() {
        return new BridgeChooseActionFlowContext() {
            @Override
            public PendingAction currentDecisionAction() {
                return BridgeCallbackHandler.this.currentDecisionAction();
            }

            @Override
            public boolean requestCannotContinue() {
                return superseded
                    || playerDead
                    || (activeGames.isEmpty() && gameEverStarted)
                    || !client.isRunning();
            }

            @Override
            public ChooseActionTool.Result noPendingActionResult() {
                var result = new ChooseActionTool.Result();
                return buildError(result, "no_pending_action", "No pending action (game over or shutting down)", false, null);
            }

            @Override
            public BridgeChooseActionStartResult applyChooseAction(BridgeChooseActionInput input, PendingAction action) {
                return BridgeCallbackHandler.this.applyChooseActionNow(input, action, false);
            }

            @Override
            public void finishChooseActionWithNextDecision(
                    ChooseActionTool.Result result,
                    PendingAction previousAction,
                    PendingAction nextAction) {
                result.game_seq = nextAction.gameSeq();
                mergeActionChoices(result, null, nextAction);
                String summary = "after=" + summarizePendingAction(previousAction)
                    + ",woke_to=" + summarizePendingAction(nextAction)
                    + ",gameOver=" + (activeGames.isEmpty() && gameEverStarted);
                logger.info("[" + client.getUsername() + "] chooseAction wakeup: " + summary);
                logBridgeEvent("CHOOSE_ACTION_WAKEUP", nextAction.gameId(), summary);
            }

            @Override
            public void finishChooseActionWithoutNextDecision(
                    ChooseActionTool.Result result,
                    PendingAction previousAction) {
                String summary = "after=" + summarizePendingAction(previousAction)
                    + ",woke_to=game_over"
                    + ",playerDead=" + playerDead
                    + ",activeGames=" + activeGames.size()
                    + ",clientRunning=" + client.isRunning();
                logger.info("[" + client.getUsername() + "] chooseAction wakeup: " + summary);
                logBridgeEvent("CHOOSE_ACTION_WAKEUP", previousAction.gameId(), summary);
                attachUnseenChat(result);
            }

            @Override
            public ChooseActionTool.Result interruptedChooseActionResult(
                    PendingAction previousAction,
                    ChooseActionTool.Result partialResult) {
                ChooseActionTool.Result result = partialResult != null ? partialResult : new ChooseActionTool.Result();
                result.success = false;
                result.error = "Interrupted while waiting for choose_action";
                result.error_code = "interrupted";
                result.retryable = false;
                attachUnseenChat(result);
                if (previousAction != null) {
                    String summary = "after=" + summarizePendingAction(previousAction) + ",woke_to=interrupted";
                    logger.info("[" + client.getUsername() + "] chooseAction wakeup: " + summary);
                    logBridgeEvent("CHOOSE_ACTION_WAKEUP", previousAction.gameId(), summary);
                }
                return result;
            }
        };
    }

    // TODO: Delete this adapter once processor-owned state/helpers move out of
    // BridgeCallbackHandler. Processor flows should depend on processor-local
    // state/services, not a broad facade back into the handler.
    private BridgePassPriorityFlowContext createPassPriorityFlowContext() {
        return new BridgePassPriorityFlowContext() {
            @Override
            public String username() {
                return client.getUsername();
            }

            @Override
            public PendingAction currentPendingAction() {
                return pendingAction;
            }

            @Override
            public PendingAction currentDecisionAction() {
                return BridgeCallbackHandler.this.currentDecisionAction();
            }

            @Override
            public PendingAction resolvePassPriorityAction(PendingAction action) {
                DecisionBoundaryTransition transition =
                    transitionToDecisionBoundary(action, "passPriority");
                return transition.status() == DecisionBoundaryStatus.READY ? transition.action() : null;
            }

            @Override
            public GameView preparePassPriorityActionView(PendingAction action) {
                if (action.data() instanceof GameClientMessage gcm) {
                    GameView gv = gcm.getGameView();
                    if (gv != null) {
                        updateLastGameView(gv, "passPriority:" + action.method().name());
                        int turn = gv.getTurn();
                        if (turn != lastTurnNumber) {
                            lastTurnNumber = turn;
                            failedManaCasts.clear();
                            interactionsThisTurn = 0;
                            poolManaAttempts = 0;
                            poolManaPayingForId = null;
                            manaPlan = null;
                            manaPlanAbilityIndex = null;
                        }
                    }
                }
                if (action.data() instanceof GameClientMessage gcm) {
                    return gcm.getGameView();
                }
                return lastGameView;
            }

            @Override
            public int interactionsThisTurn() {
                return interactionsThisTurn;
            }

            @Override
            public int maxInteractionsPerTurn() {
                return maxInteractionsPerTurn;
            }

            @Override
            public void executeDefaultAction() {
                BridgeCallbackHandler.this.executeDefaultAction();
            }

            @Override
            public String detectCombatSelect(PendingAction action) {
                return BridgeCallbackHandler.this.detectCombatSelect(action);
            }

            @Override
            public ActionResult pendingActionResult(PendingAction action, String stopReason, Long boardCursorParam) {
                return BridgeCallbackHandler.this.pendingActionResult(action, stopReason, boardCursorParam);
            }

            @Override
            public ActionResult pendingActionResult(
                    PendingAction action,
                    String stopReason,
                    Long boardCursorParam,
                    Consumer<ActionResult> customizer) {
                return BridgeCallbackHandler.this.pendingActionResult(action, stopReason, boardCursorParam, customizer);
            }

            @Override
            public ActionResult stepYieldResult(PendingAction action, GameView gameView, String stopReason, Long boardCursorParam) {
                return BridgeCallbackHandler.this.stepYieldResult(action, gameView, stopReason, boardCursorParam);
            }

            @Override
            public ActionResult stackResolvedResult(PendingAction action, Long boardCursorParam) {
                return BridgeCallbackHandler.this.stackResolvedResult(action, boardCursorParam);
            }

            @Override
            public UUID lowestStackObjectId(GameView gameView) {
                return BridgeCallbackHandler.this.lowestStackObjectId(gameView);
            }

            @Override
            public boolean stackContains(GameView gameView, UUID stackObjectId) {
                return BridgeCallbackHandler.this.stackContains(gameView, stackObjectId);
            }

            @Override
            public boolean clearPendingActionIfCurrent(PendingAction action) {
                return BridgeCallbackHandler.this.clearPendingActionIfCurrent(action);
            }

            @Override
            public void sendBooleanOrDie(UUID gameId, boolean data, String sendContext) {
                BridgeCallbackHandler.this.sendBooleanOrDie(gameId, data, sendContext);
            }

            @Override
            public UUID currentGameId() {
                return currentGameId;
            }

            @Override
            public GameView lastGameView() {
                return lastGameView;
            }

            @Override
            public int lastTurnNumber() {
                return lastTurnNumber;
            }

            @Override
            public int activeGamesSize() {
                return activeGames.size();
            }

            @Override
            public boolean superseded() {
                return superseded;
            }

            @Override
            public boolean playerDead() {
                return playerDead;
            }

            @Override
            public boolean gameEverStarted() {
                return gameEverStarted;
            }

            @Override
            public boolean clientRunning() {
                return client.isRunning();
            }

            @Override
            public long lastActionableCallbackAt() {
                return lastActionableCallbackAt;
            }

            @Override
            public long lastCallbackReceivedAt() {
                return lastCallbackReceivedAt;
            }

            @Override
            public void declareZombieGame(long absoluteIdleMs) {
                logger.error("[" + client.getUsername() + "] Zombie game detected: "
                    + "no actionable callback for " + absoluteIdleMs + "ms, declaring game dead");
                logError("Zombie game detected: no actionable callback for " + absoluteIdleMs + "ms");
                playerDead = true;
            }

            @Override
            public boolean failedManaCast(UUID objectId) {
                return failedManaCasts.contains(objectId);
            }

            @Override
            public void finalizePassPriorityResult(
                    BridgePassPriorityFlow flow,
                    String until,
                    int actionsPassed,
                    PendingAction action,
                    GameView view,
                    ActionResult result,
                    boolean actionPending) {
                if (pendingPassPriorityFlow == flow) {
                    pendingPassPriorityFlow = null;
                }
                attachUnseenChat(result);
                logPassPriorityReturn(until, actionsPassed, action, view, result, actionPending);
            }
        };
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
    private void sendBooleanOrDie(UUID gameId, boolean data, String context) {
        boolean ok = session.sendPlayerBoolean(gameId, data);
        if (!ok) {
            declareResponseFailed("sendPlayerBoolean(" + data + ")", context, gameId);
        }
    }

    private void sendUuidOrDie(UUID gameId, UUID data, String context) {
        boolean ok = session.sendPlayerUUID(gameId, data);
        if (!ok) {
            declareResponseFailed("sendPlayerUUID(" + data + ")", context, gameId);
        }
    }

    private void sendStringOrDie(UUID gameId, String data, String context) {
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
    static class ResponseDeliveryException extends RuntimeException {
        ResponseDeliveryException(String message) {
            super(message);
        }
    }

    private void declareResponseFailed(String call, String context, UUID gameId) {
        String msg = call + " failed — server did not receive response"
            + " (context=" + context + ", gameId=" + gameId + ")";
        logger.error("[" + client.getUsername() + "] CRITICAL: " + msg);
        logError(msg);
        playerDead = true;
        synchronized (actionLock) {
            actionLock.notifyAll();
        }
        throw new ResponseDeliveryException(msg);
    }

    private final class ActionableCallbackOutcome implements BridgeActionableCallbackOutcome {
        private final ClientCallbackMethod method;
        private String outcome = null;

        private ActionableCallbackOutcome(ClientCallbackMethod method) {
            this.method = method;
        }

        @Override
        public void storedPendingAction(String detail) {
            record("stored_pending_action:" + detail);
        }

        @Override
        public void sentResponse(String detail) {
            record("sent_response:" + detail);
        }

        @Override
        public void verifyRecorded() {
            if (outcome == null) {
                throw new IllegalStateException(
                        "Actionable callback " + method
                        + " returned without storing a pending action or sending a response");
            }
        }

        private void record(String nextOutcome) {
            if (outcome != null) {
                throw new IllegalStateException(
                        "Actionable callback " + method
                        + " recorded multiple outcomes: " + outcome + " then " + nextOutcome);
            }
            outcome = nextOutcome;
            logger.debug("[" + client.getUsername() + "] Callback outcome " + method + ": " + nextOutcome);
            logBridgeEvent("CALLBACK_OUTCOME", method.name() + ": " + nextOutcome);
        }
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
        logBridgeEvent(method, currentGameId, summary);
    }

    private void logBridgeEvent(ClientCallbackMethod method, UUID gameId, String summary) {
        logBridgeEvent(method.name(), gameId, summary);
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

    private String summarizeCallbackContext(UUID callbackGameId, String ignoreReason) {
        PendingAction action = pendingAction;
        boolean callbackActive = callbackGameId != null && activeGames.containsKey(callbackGameId);
        var sb = new StringBuilder();
        sb.append("callbackGameId=").append(callbackGameId);
        sb.append(",currentGameId=").append(currentGameId);
        sb.append(",callbackActive=").append(callbackActive);
        sb.append(",pendingAction=").append(summarizePendingAction(action));
        if (ignoreReason != null) {
            sb.append(",ignoreReason=").append(ignoreReason);
        }
        return sb.toString();
    }

    private void logCallbackReceived(UUID callbackGameId, ClientCallbackMethod method, String ignoreReason) {
        String summary = summarizeCallbackContext(callbackGameId, ignoreReason);
        logger.debug("[" + client.getUsername() + "] Callback received: " + method + " (" + summary + ")");
        logBridgeEvent("CALLBACK_RECEIVED", callbackGameId, method.name() + " | " + summary);
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
            + ",pendingAction=" + summarizePendingAction(pendingAction);
        logger.info("[" + client.getUsername() + "] passPriority RETURN: " + summary);
        logBridgeEvent("PASS_PRIORITY_RETURN", action != null ? action.gameId() : currentGameId, summary);
    }

    /**
     * Build a compact one-line summary of game state for bridge JSONL dump.
     */
    private String buildBridgeStateSummary() {
        GameView gv = lastGameView;
        if (gv == null) {
            return null;
        }
        var sb = new StringBuilder();
        sb.append("T").append(roundTracker.getGameRound());
        if (gv.getPhase() != null) sb.append(" ").append(gv.getPhase());
        sb.append(" | ");
        UUID gameId = currentGameId; // snapshot volatile to prevent TOCTOU race
        UUID myPlayerId = playerIdForGame(gameId);
        for (PlayerView p : gv.getPlayers()) {
            boolean isMe = p.getPlayerId().equals(myPlayerId);
            sb.append(p.getName());
            if (isMe) sb.append("(me)");
            sb.append(":").append(p.getLife()).append("hp");
            sb.append(",").append(p.getHandCount()).append("h");
            sb.append(",").append(p.getBattlefield() != null ? p.getBattlefield().size() : 0).append("bf");
            sb.append(" | ");
        }
        // My hand
        if (gv.getMyHand() != null && !gv.getMyHand().isEmpty()) {
            sb.append("Hand:[");
            boolean first = true;
            for (CardView card : gv.getMyHand().values()) {
                if (!first) sb.append(",");
                sb.append(card.getDisplayName());
                first = false;
            }
            sb.append("]");
        }
        return sb.toString();
    }

    private UUID playerIdForGame(UUID gameId) {
        if (gameId == null) {
            return null;
        }
        UUID playerId = activeGames.get(gameId);
        if (playerId != null) {
            return playerId;
        }
        return gameId.equals(currentGameId) ? currentPlayerId : null;
    }

    public void setSession(Session session) {
        this.session = session;
    }

    public void setKeepAliveAfterGame(boolean keepAliveAfterGame) {
        this.keepAliveAfterGame = keepAliveAfterGame;
        logger.info("[" + client.getUsername() + "] keepAliveAfterGame=" + keepAliveAfterGame);
    }

    public void setDeckList(DeckCardLists deckList) {
        this.deckList = deckList;
    }

    public void setMaxInteractionsPerTurn(int max) {
        this.maxInteractionsPerTurn = Math.max(5, max);
        logger.info("[" + client.getUsername() + "] maxInteractionsPerTurn set to " + this.maxInteractionsPerTurn);
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
        this.superseded = true;
        synchronized (actionLock) {
            actionLock.notifyAll();
        }
        advancePendingFlowsBeforeShutdown();
        processor.shutdown("superseded by createFreshForNextGame");

        BridgeCallbackHandler fresh = new BridgeCallbackHandler(client);
        fresh.session = this.session;
        fresh.keepAliveAfterGame = this.keepAliveAfterGame;
        fresh.maxInteractionsPerTurn = this.maxInteractionsPerTurn;
        fresh.errorLogPath = this.errorLogPath;
        fresh.bridgeLogPath = this.bridgeLogPath;
        fresh.joinHandler = this.joinHandler;
        client.setCallbackHandler(fresh);
        logger.info("[" + client.getUsername() + "] Created fresh handler for next game");
        return fresh;
    }

    /**
     * Block until {@code handleStartGame()} fires. Used by join_table tool.
     * @return true if game started, false if timed out
     */
    public boolean awaitGameStart(long timeoutMs) throws InterruptedException {
        return gameStartLatch.await(timeoutMs, java.util.concurrent.TimeUnit.MILLISECONDS);
    }

    /**
     * Block until {@code handleGameOver()} fires. Used by keepAlive session management.
     * @return true if game finished, false if timed out
     */
    public boolean awaitGameFinished(long timeoutMs) throws InterruptedException {
        return gameFinishedLatch.await(timeoutMs, java.util.concurrent.TimeUnit.MILLISECONDS);
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
        fresh.startGameArmed = true;
        // Set expectedStartTableId BEFORE joining so stale START_GAME callbacks
        // (from server reconnection replaying old games) are rejected during the
        // window between createFreshForNextGame() and jh.joinTable().
        if (targetTableId != null) {
            fresh.expectedStartTableId = targetTableId;
        }
        UUID tableId = jh.joinTable(deckPath, targetTableId);
        assert tableId != null : "Failed to join any table within timeout";
        fresh.expectedStartTableId = tableId;
        logger.info("[" + client.getUsername() + "] Joined table " + tableId + ", waiting for game start...");
        boolean started = fresh.awaitGameStart(60_000);
        assert started : "Game did not start within 60s after joining table";
        logger.info("[" + client.getUsername() + "] Game started after join_table");
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
        activeGames.clear();
        gameChatIds.clear();
        pendingAction = null;
        currentGameId = null;
        currentPlayerId = null;
        gameEverStarted = false;
        lastGameView = null;
        lastChoices = null;
        lastActionableCallbackAt = 0;
        cachedBridgeEvents.clear();
        bridgeEventCursor = 0;
        synchronized (chatLog) {
            chatLog.clear();
        }
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
        advancePendingFlowsBeforeShutdown();
        processor.shutdown(reason);
        synchronized (actionLock) {
            actionLock.notifyAll();
        }
    }

    public boolean isActionPending() {
        return pendingAction != null;
    }

    public Map<String, Object> executeDefaultAction() {
        return processor.submit(BridgeCommand.of(this::executeDefaultActionImpl));
    }

    private Map<String, Object> executeDefaultActionImpl() {
        var result = new HashMap<String, Object>();
        PendingAction action = pendingAction;
        if (action == null) {
            result.put("success", false);
            result.put("error", "No pending action");
            attachUnseenChat(result);
            return result;
        }

        // Clear pending action only if it hasn't been overwritten by a new callback.
        synchronized (actionLock) {
            if (pendingAction == action) {
                pendingAction = null;
            }
        }

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
     *
     * Throws ResponseDeliveryException if auto-resolve triggers a send that fails,
     * so internal callers (chooseAction, attachChoicesToError) see the transport failure.
     * MCP tool boundary should use {@link #getActionChoicesSafe} instead.
     */
    @SuppressWarnings("unchecked")
    public ActionResult getActionChoices(Long boardCursorParam) {
        return processor.submit(BridgeCommand.of(() -> getActionChoicesImpl(boardCursorParam)));
    }

    @SuppressWarnings("unchecked")
    private ActionResult getActionChoicesImpl(Long boardCursorParam) {
        PendingAction action = pendingAction;
        ActionResult result = buildActionChoices(action, boardCursorParam, true);
        if (action == null) {
            attachUnseenChat(result);
        }
        return result;
    }

    /**
     * MCP tool boundary wrapper: catches ResponseDeliveryException and converts
     * it to an ActionResult error instead of letting it propagate as an exception.
     */
    public ActionResult getActionChoicesSafe(Long boardCursorParam) {
        try {
            return getActionChoices(boardCursorParam);
        } catch (ResponseDeliveryException e) {
            var result = new ActionResult();
            result.error = e.getMessage();
            attachUnseenChat(result);
            return result;
        }
    }

    @SuppressWarnings("unchecked")
    private ActionResult buildActionChoices(PendingAction action, Long boardCursorParam, boolean allowAutoResolve) {
        var result = new ActionResult();
        // Prefer the action's own GameView over lastGameView — a concurrent GAME_UPDATE
        // can overwrite lastGameView with a view from a different phase (race condition).
        GameView gameView = null;
        if (action != null && action.data() instanceof GameClientMessage gcm) {
            gameView = gcm.getGameView();
        }
        if (gameView == null) {
            gameView = lastGameView;
        }
        // Capture for use in lambdas (must be effectively final).
        final GameView gv = gameView;
        if (action != null) {
            result.game_seq = action.gameSeq();
        }

        if (action == null) {
            result.action_pending = false;
            clearChoiceSnapshot();
            return result;
        }

        result.action_pending = true;
        result.action_type = action.method().name();
        result.message = stripHtml(action.message());

        // Add compact phase context and player summary
        if (gameView != null) {
            int turn = roundTracker.update(gameView);
            boolean isMyTurn = client.getUsername().equals(gameView.getActivePlayerName());
            boolean isMainPhase = gameView.getPhase() != null && gameView.getPhase().isMain();

            var ctx = new StringBuilder();
            ctx.append("T").append(turn);
            if (gameView.getPhase() != null) {
                ctx.append(" ").append(gameView.getPhase());
            }
            if (gameView.getStep() != null) {
                ctx.append("/").append(gameView.getStep());
            }
            ctx.append(" (").append(gameView.getActivePlayerName()).append(")");
            if (isMyTurn && isMainPhase) {
                ctx.append(" YOUR_MAIN");
            }
            result.context = ctx.toString();

            // Full board state: players with battlefield, graveyard, exile, hand, etc.
            // Board cursor dedup: skip the board payload when caller already has it.
            List<Map<String, Object>> players = buildPlayersArray(gameView);
            long currentBoardCursor = updateBoardCursor(players);
            result.board_cursor = currentBoardCursor;
            if (boardCursorParam != null && boardCursorParam.longValue() == currentBoardCursor) {
                result.board_unchanged = true;
            } else {
                result.board = players;
            }

            // Convenience top-level fields (also available per-player in board)
            PlayerView myPlayer = gameView.getMyPlayer();
            if (myPlayer != null && myPlayer.getBattlefield() != null) {
                int untappedLands = 0;
                for (PermanentView perm : myPlayer.getBattlefield().values()) {
                    if (perm.isLand() && !perm.isTapped()) {
                        untappedLands++;
                    }
                }
                if (untappedLands > 0) {
                    result.untapped_lands = untappedLands;
                }
            }
            // Analogous to Arena highlighting your lands when you have a land drop left.
            // Helps LLMs remember they can play a land this turn.
            // Uses the authoritative server value from PlayerView, not chat-message counting.
            if (isMyTurn && isMainPhase && myPlayer != null) {
                result.land_drops_used = myPlayer.getLandsPlayed();
            }

            // Stack summary — helps LLMs see what's pending before casting instants/counters
            List<Map<String, Object>> stackSummary = buildStackItems(gameView, false, false);
            if (!stackSummary.isEmpty()) {
                result.stack = stackSummary;
            }

            // Combat context — show attackers/blockers during any combat step
            // so LLMs see the combat state when casting instants or activating abilities
            List<Map<String, Object>> combatGroups = buildCombatGroups(gameView);
            if (combatGroups != null) {
                result.combat = combatGroups;
            }
        }

        ClientCallbackMethod method = action.method();
        Object data = action.data();

        switch (method) {
            case GAME_ASK: {
                result.response_type = "boolean";
                result.respond_with = "choice=yes or choice=no";
                lastChoices = null;

                // For mulligan decisions, include hand contents so LLM can evaluate
                String askMsg = action.message();
                if (askMsg != null && askMsg.toLowerCase().contains("mulligan") && gameView != null) {
                    CardsView hand = gameView.getMyHand();
                    if (hand != null && !hand.isEmpty()) {
                        // Sort hand by card name for deterministic ordering
                        var sortedHand = new ArrayList<>(hand.values());
                        sortedHand.sort(Comparator.comparing(c -> safeDisplayName(c)));

                        var handCards = new ArrayList<Map<String, Object>>();
                        for (CardView card : sortedHand) {
                            handCards.add(buildCardInfoMap(card));
                        }
                        result.your_hand = handCards;
                    }
                }
                break;
            }

            case GAME_SELECT: {
                // Check for playable cards in the current game view
                PlayableObjectsList playable = gameView != null ? gameView.getCanPlayObjects() : null;
                var choiceList = new ArrayList<Map<String, Object>>();
                var indexToUuid = new ArrayList<Object>();

                if (playable != null && !playable.isEmpty()) {
                    // Clear failed casts and loop counters on turn change
                    if (gameView != null) {
                        int turn = gameView.getTurn();
                        if (turn != lastTurnNumber) {
                            lastTurnNumber = turn;
                            failedManaCasts.clear();
                            interactionsThisTurn = 0;
                            poolManaAttempts = 0;
                            poolManaPayingForId = null;
                            manaPlan = null;
                            manaPlanAbilityIndex = null;
                        }
                    }

                    // Sort playable objects by card name for deterministic ordering
                    // (HashMap iteration order depends on UUID hashCodes, which vary across JVM runs)
                    var sortedPlayable = new ArrayList<>(playable.getObjects().entrySet());
                    sortedPlayable.sort(Comparator.<Map.Entry<UUID, PlayableObjectStats>, String>comparing(e -> {
                        CardView cv = findCardViewById(e.getKey(), gv);
                        return cv != null ? safeDisplayName(cv) : "";
                    }).thenComparingInt(e -> getStableShortIdSequence(e.getKey(), findCardViewById(e.getKey(), gv))));

                    int idx = 0;
                    for (Map.Entry<UUID, PlayableObjectStats> entry : sortedPlayable) {
                        UUID objectId = entry.getKey();
                        PlayableObjectStats stats = entry.getValue();

                        // Skip spells that failed mana payment (can't afford them)
                        if (failedManaCasts.contains(objectId)) {
                            continue;
                        }

                        // Skip objects whose only abilities are mana abilities
                        // (mana payment is handled during GAME_PLAY_MANA, not GAME_SELECT)
                        List<String> abilityNames = stats.getPlayableAbilityNames();
                        List<String> manaNames = stats.getAllManaAbilityNames();
                        if (!abilityNames.isEmpty() && manaNames.size() == abilityNames.size()) {
                            continue;
                        }

                        // Determine where this object lives (hand = cast, battlefield = activate)
                        CardView cardView = findCardViewById(objectId, gameView);
                        var choiceEntry = new HashMap<String, Object>();
                        choiceEntry.put("index", idx);
                        choiceEntry.put("id", getStableShortId(objectId, cardView));

                        boolean isOnBattlefield = false;
                        if (cardView == null) {
                            // not found in hand/stack, check battlefield directly
                            isOnBattlefield = true;
                        } else if (gameView.getMyHand().get(objectId) == null
                                   && gameView.getStack().get(objectId) == null) {
                            isOnBattlefield = true;
                        }

                        if (cardView != null) {
                            choiceEntry.put("name", safeDisplayName(cardView));
                            if (isOnBattlefield) {
                                choiceEntry.put("action", "activate");
                                // Filter out mana abilities
                                var manaNameSet = new HashSet<>(stats.getAllManaAbilityNames());
                                var nonManaAbilities = new ArrayList<String>();
                                for (String name : abilityNames) {
                                    if (!manaNameSet.contains(name)) {
                                        nonManaAbilities.add(name);
                                    }
                                }
                                if (!nonManaAbilities.isEmpty()) {
                                    choiceEntry.put("playable_abilities", nonManaAbilities);
                                }
                            } else {
                                if (cardView.isLand()) {
                                    choiceEntry.put("action", "land");
                                } else {
                                    choiceEntry.put("action", "cast");
                                }
                                String manaCost = cardView.getManaCostStr();
                                if (manaCost != null && !manaCost.isEmpty()) {
                                    choiceEntry.put("mana_cost", manaCost);
                                }
                                if (cardView.isCreature() && cardView.getPower() != null) {
                                    choiceEntry.put("power", cardView.getPower());
                                    choiceEntry.put("toughness", cardView.getToughness());
                                }
                            }
                        } else {
                            choiceEntry.put("name", "Unknown (" + objectId.toString().substring(0, 8) + ")");
                        }

                        choiceList.add(choiceEntry);
                        indexToUuid.add(objectId);
                        idx++;
                    }
                }

                // Check for combat selections (declare attackers / declare blockers)
                if (data instanceof GameClientMessage gcm) {
                    Map<String, Serializable> options = gcm.getOptions();
                    if (options != null) {
                        @SuppressWarnings("unchecked")
                        List<UUID> possibleAttackerIds = (List<UUID>) options.get("possibleAttackers");
                        @SuppressWarnings("unchecked")
                        List<UUID> possibleBlockerIds = (List<UUID>) options.get("possibleBlockers");

                        if (possibleAttackerIds != null && !possibleAttackerIds.isEmpty()) {
                            result.combat_phase = "declare_attackers";

                            // Show which creatures are already attacking
                            var alreadyAttacking = new ArrayList<Map<String, Object>>();
                            if (gameView != null && gameView.getCombat() != null) {
                                for (CombatGroupView group : gameView.getCombat()) {
                                    for (CardView attacker : group.getAttackers().values()) {
                                        var aInfo = new HashMap<String, Object>();
                                        if (attacker.getId() != null) {
                                            aInfo.put("id", getStableShortId(attacker.getId(), attacker));
                                        }
                                        aInfo.put("name", safeDisplayName(attacker));
                                        if (attacker.getPower() != null) {
                                            aInfo.put("power", attacker.getPower());
                                            aInfo.put("toughness", attacker.getToughness());
                                        }
                                        alreadyAttacking.add(aInfo);
                                    }
                                }
                            }
                            if (!alreadyAttacking.isEmpty()) {
                                result.already_attacking = alreadyAttacking;
                            }

                            int idx = choiceList.size();
                            for (UUID attackerId : possibleAttackerIds) {
                                PermanentView perm = findPermanentViewById(attackerId, gameView);
                                if (perm == null) continue;

                                var choiceEntry = new HashMap<String, Object>();
                                choiceEntry.put("index", idx);
                                choiceEntry.put("id", getStableShortId(attackerId, perm));
                                choiceEntry.put("name", safeDisplayName(perm));
                                if (perm.getPower() != null) {
                                    choiceEntry.put("power", perm.getPower());
                                    choiceEntry.put("toughness", perm.getToughness());
                                }
                                choiceEntry.put("choice_type", "attacker");
                                choiceList.add(choiceEntry);
                                indexToUuid.add(attackerId);
                                idx++;
                            }

                            // Add "All attack" special option if available
                            if (options.containsKey("specialButton")) {
                                var allAttackEntry = new HashMap<String, Object>();
                                allAttackEntry.put("index", idx);
                                allAttackEntry.put("id", "all");
                                allAttackEntry.put("name", "All attack");
                                allAttackEntry.put("choice_type", "special");
                                choiceList.add(allAttackEntry);
                                indexToUuid.add("special");
                                idx++;
                            }
                        }

                        if (possibleBlockerIds != null && !possibleBlockerIds.isEmpty()) {
                            result.combat_phase = "declare_blockers";

                            // Show attacking creatures for context
                            var incomingAttackers = new ArrayList<Map<String, Object>>();
                            if (gameView != null && gameView.getCombat() != null) {
                                for (CombatGroupView group : gameView.getCombat()) {
                                    for (CardView attacker : group.getAttackers().values()) {
                                        var aInfo = new HashMap<String, Object>();
                                        if (attacker.getId() != null) {
                                            aInfo.put("id", getStableShortId(attacker.getId(), attacker));
                                        }
                                        aInfo.put("name", attacker.getDisplayName());
                                        if (attacker.getPower() != null) {
                                            aInfo.put("power", attacker.getPower());
                                            aInfo.put("toughness", attacker.getToughness());
                                        }
                                        incomingAttackers.add(aInfo);
                                    }
                                }
                            }
                            if (!incomingAttackers.isEmpty()) {
                                result.incoming_attackers = incomingAttackers;
                            }

                            int idx = choiceList.size();
                            for (UUID blockerId : possibleBlockerIds) {
                                PermanentView perm = findPermanentViewById(blockerId, gameView);
                                if (perm == null) continue;

                                var choiceEntry = new HashMap<String, Object>();
                                choiceEntry.put("index", idx);
                                choiceEntry.put("id", getStableShortId(blockerId, perm));
                                choiceEntry.put("name", safeDisplayName(perm));
                                if (perm.getPower() != null) {
                                    choiceEntry.put("power", perm.getPower());
                                    choiceEntry.put("toughness", perm.getToughness());
                                }
                                choiceEntry.put("choice_type", "blocker");
                                choiceList.add(choiceEntry);
                                indexToUuid.add(blockerId);
                                idx++;
                            }
                        }
                    }
                }

                if (!choiceList.isEmpty()) {
                    result.response_type = "select";
                    result.choices = choiceList;
                    lastChoices = indexToUuid;
                    String combatPhase = result.combat_phase;
                    if ("declare_attackers".equals(combatPhase)) {
                        result.respond_with = "attackers=p1,p2,... or choice=yes (confirm) or choice=no (skip)";
                    } else if ("declare_blockers".equals(combatPhase)) {
                        result.respond_with = "blockers=p5:p1,p6:p2 (blocker:attacker) or choice=yes (confirm) or choice=no (skip)";
                    } else {
                        result.respond_with = "choice=pN to play, or choice=no to pass";
                    }
                } else {
                    result.response_type = "boolean";
                    result.respond_with = "choice=yes (confirm) or choice=no (pass)";
                    lastChoices = null;
                }
                break;
            }

            case GAME_PLAY_MANA:
            case GAME_PLAY_XMANA: {
                // Auto-tap couldn't find a source — show available mana sources to the LLM
                GameClientMessage manaMsg = (GameClientMessage) data;
                PlayableObjectsList manaPlayable = gameView != null ? gameView.getCanPlayObjects() : null;
                var manaChoiceList = new ArrayList<Map<String, Object>>();
                var manaIndexToChoice = new ArrayList<Object>();
                UUID payingForId = extractPayingForId(manaMsg.getMessage());

                if (manaPlayable != null) {
                    // Sort mana sources by card name for deterministic ordering
                    var sortedManaEntries = new ArrayList<>(manaPlayable.getObjects().entrySet());
                    sortedManaEntries.sort(Comparator.<Map.Entry<UUID, PlayableObjectStats>, String>comparing(e -> {
                        CardView cv = findCardViewById(e.getKey(), gv);
                        return cv != null ? safeDisplayName(cv) : "";
                    }).thenComparingInt(e -> getStableShortIdSequence(e.getKey(), findCardViewById(e.getKey(), gv))));

                    int idx = 0;
                    for (Map.Entry<UUID, PlayableObjectStats> entry : sortedManaEntries) {
                        UUID manaObjectId = entry.getKey();
                        if (manaObjectId.equals(payingForId)) {
                            continue;
                        }
                        PlayableObjectStats stats = entry.getValue();
                        List<String> manaAbilities = stats.getAllManaAbilityNames();
                        if (manaAbilities.isEmpty()) {
                            continue;
                        }

                        CardView cardView = findCardViewById(manaObjectId, gameView);
                        String cardName;
                        if (cardView != null) {
                            cardName = cardView.getDisplayName();
                        } else {
                            cardName = "Unknown (" + manaObjectId.toString().substring(0, 8) + ")";
                        }

                        for (String manaAbilityText : manaAbilities) {
                            var choiceEntry = new HashMap<String, Object>();
                            choiceEntry.put("index", idx);
                            choiceEntry.put("id", getStableShortId(manaObjectId, cardView));
                            boolean isTap = manaAbilityText.contains("{T}");
                            choiceEntry.put("choice_type", isTap ? "tap_source" : "mana_source");
                            choiceEntry.put("name", cardName);
                            choiceEntry.put("ability", manaAbilityText);
                            manaChoiceList.add(choiceEntry);
                            manaIndexToChoice.add(manaObjectId);
                            idx++;
                        }
                    }
                }

                List<ManaType> poolChoices = getPoolManaChoices(gameView, manaMsg.getMessage());
                if (!poolChoices.isEmpty()) {
                    int idx = manaChoiceList.size();
                    ManaPoolView manaPool = getMyManaPoolView(gameView);
                    for (ManaType manaType : poolChoices) {
                        var choiceEntry = new HashMap<String, Object>();
                        choiceEntry.put("index", idx);
                        choiceEntry.put("choice_type", "pool_mana");
                        choiceEntry.put("name", prettyManaType(manaType));
                        choiceEntry.put("count", getManaPoolCount(manaPool, manaType));
                        manaChoiceList.add(choiceEntry);
                        manaIndexToChoice.add(manaType);
                        idx++;
                    }
                }

                if (!manaChoiceList.isEmpty()) {
                    result.response_type = "select";
                    result.respond_with = "choice=pN to tap, or choice=no to cancel";
                    result.choices = manaChoiceList;
                    lastChoices = manaIndexToChoice;
                } else {
                    result.response_type = "boolean";
                    result.respond_with = "choice=no to cancel";
                    lastChoices = null;
                }
                break;
            }

            case GAME_TARGET: {
                GameClientMessage msg = (GameClientMessage) data;
                result.response_type = "index";
                boolean required = msg.isFlag();
                result.required = required;
                result.can_cancel = !required;
                result.respond_with = required
                    ? "choice=pN — must pick a target"
                    : "choice=pN, or choice=no to cancel";

                Set<UUID> targets = findValidTargets(msg);
                var choiceList = new ArrayList<Map<String, Object>>();
                var indexToUuid = new ArrayList<Object>();

                if (targets != null) {
                    CardsView cardsView = msg.getCardsView1();
                    GameView targetGameView = msg.getGameView() != null ? msg.getGameView() : lastGameView;
                    UUID gameId = currentGameId;
                    UUID myPlayerId = playerIdForGame(gameId);
                    var targetChoices = new ArrayList<TargetChoice>();
                    for (UUID targetId : targets) {
                        var choiceEntry = new HashMap<String, Object>();
                        // ID assigned after sorting — see below
                        CardView resolvedCv = buildTargetInfo(choiceEntry, targetId, cardsView, targetGameView, myPlayerId);
                        targetChoices.add(new TargetChoice(targetId, choiceEntry, resolvedCv));
                    }

                    // Sort all target choices deterministically: "you" first, then alphabetical.
                    // HashMap iteration order depends on UUID hashCodes which vary across JVM runs.
                    // IDs are assigned AFTER sorting so they're deterministic.
                    targetChoices.sort((a, b) -> {
                        boolean aIsYou = Boolean.TRUE.equals(a.entry().get("is_you"));
                        boolean bIsYou = Boolean.TRUE.equals(b.entry().get("is_you"));
                        int youCmp = Boolean.compare(bIsYou, aIsYou);
                        if (youCmp != 0) {
                            return youCmp;
                        }
                        String aName = Objects.toString(a.entry().get("name"), "");
                        String bName = Objects.toString(b.entry().get("name"), "");
                        int nameCmp = String.CASE_INSENSITIVE_ORDER.compare(aName, bName);
                        if (nameCmp != 0) {
                            return nameCmp;
                        }
                        return Integer.compare(
                            getStableShortIdSequence(a.targetId(), a.cardView()),
                            getStableShortIdSequence(b.targetId(), b.cardView()));
                    });

                    int idx = 0;
                    for (TargetChoice tc : targetChoices) {
                        tc.entry().put("id", getStableShortId(tc.targetId(), tc.cardView()));
                        tc.entry().put("index", idx);
                        choiceList.add(tc.entry());
                        indexToUuid.add(tc.targetId());
                        idx++;
                    }
                }

                // Optional GAME_TARGET with no valid targets: auto-cancel
                if (choiceList.isEmpty() && !required && allowAutoResolve) {
                    clearPendingActionIfCurrent(action);
                    sendBooleanOrDie(action.gameId(), false, "buildActionChoices:auto_cancel_no_targets");
                    result.action_pending = false;
                    result.action_taken = "auto_cancelled_no_targets";
                    result.message = stripHtml(msg.getMessage());
                    lastChoices = null;
                    break;
                }

                result.choices = choiceList;
                lastChoices = indexToUuid;
                break;
            }

            case GAME_CHOOSE_ABILITY: {
                AbilityPickerView picker = (AbilityPickerView) data;
                Map<UUID, String> choices = picker.getChoices();
                result.response_type = "index";
                result.respond_with = "choice=0, choice=1, etc. (not yes/no)";

                var choiceList = new ArrayList<Map<String, Object>>();
                var indexToUuid = new ArrayList<Object>();

                boolean allManaAbilities = choices != null && !choices.isEmpty();
                if (choices != null) {
                    int idx = 0;
                    for (Map.Entry<UUID, String> entry : choices.entrySet()) {
                        var choiceEntry = new HashMap<String, Object>();
                        choiceEntry.put("index", idx);
                        String desc = stripAbilityPickerOrdinalPrefix(stripHtml(entry.getValue()), idx);
                        choiceEntry.put("description", desc);
                        choiceList.add(choiceEntry);
                        indexToUuid.add(entry.getKey());
                        idx++;
                        // Check if this looks like a mana ability (e.g. "{T}: Add {W}.")
                        if (!desc.contains("Add {")) {
                            allManaAbilities = false;
                        }
                    }
                }

                // When all choices are mana abilities, rewrite the message to clarify
                // this is a mana payment step, not a game action choice. Models often
                // get confused when they see "Choose spell or ability" during mana payment.
                if (allManaAbilities) {
                    String msg = result.message;
                    if (msg != null && msg.startsWith("Choose spell or ability")) {
                        // Extract the card name after ": " (from stripHtml's <br> replacement)
                        int colonIdx = msg.indexOf(": ");
                        String cardName = colonIdx >= 0 ? msg.substring(colonIdx + 2).trim() : "";
                        if (!cardName.isEmpty()) {
                            result.message = "Choose which mana to produce from " + cardName
                                    + " (tapping to pay for a spell)";
                        }
                    }
                }

                result.choices = choiceList;
                lastChoices = indexToUuid;
                break;
            }

            case GAME_CHOOSE_CHOICE: {
                GameClientMessage msg = (GameClientMessage) data;
                Choice choice = msg.getChoice();
                result.response_type = "index";
                result.respond_with = "choice=0, choice=1, etc. or text=Name (not yes/no)";

                var choiceList = new ArrayList<Map<String, Object>>();
                var indexToKey = new ArrayList<Object>();

                if (choice != null) {
                    if (choice.isKeyChoice()) {
                        Map<String, String> keyChoices = choice.getKeyChoices();
                        if (keyChoices != null) {
                            int idx = 0;
                            for (Map.Entry<String, String> entry : keyChoices.entrySet()) {
                                var choiceEntry = new HashMap<String, Object>();
                                choiceEntry.put("index", idx);
                                choiceEntry.put("description", stripHtml(entry.getValue()));
                                choiceList.add(choiceEntry);
                                indexToKey.add(entry.getKey());
                                idx++;
                            }
                        }
                    } else {
                        Set<String> choices = choice.getChoices();
                        if (choices != null) {
                            int idx = 0;
                            for (String c : choices) {
                                var choiceEntry = new HashMap<String, Object>();
                                choiceEntry.put("index", idx);
                                choiceEntry.put("description", c);
                                choiceList.add(choiceEntry);
                                indexToKey.add(c);
                                idx++;
                            }
                        }
                    }
                }

                // Filter large choice lists to deck-relevant options
                int totalChoices = choiceList.size();
                if (totalChoices >= 50 && deckList != null) {
                    Set<String> deckTypes = getDeckCreatureTypes();
                    if (!deckTypes.isEmpty()) {
                        var filtered = new ArrayList<Map<String, Object>>();
                        var filteredKeys = new ArrayList<Object>();
                        int idx = 0;
                        for (int i = 0; i < choiceList.size(); i++) {
                            String desc = (String) choiceList.get(i).get("description");
                            if (deckTypes.contains(desc)) {
                                var entry = new HashMap<String, Object>();
                                entry.put("index", idx);
                                entry.put("description", desc);
                                filtered.add(entry);
                                filteredKeys.add(indexToKey.get(i));
                                idx++;
                            }
                        }
                        if (!filtered.isEmpty()) {
                            choiceList = filtered;
                            indexToKey = filteredKeys;
                            result.note = "Showing " + filtered.size()
                                + " types from your deck (" + totalChoices
                                + " total available). Use choose_action(text='TypeName') for any other type.";
                        }
                    }
                }

                result.choices = choiceList;
                lastChoices = indexToKey;
                break;
            }

            case GAME_CHOOSE_PILE: {
                GameClientMessage msg = (GameClientMessage) data;
                result.response_type = "pile";
                result.respond_with = "pile=1 or pile=2";

                var pile1 = new ArrayList<Map<String, Object>>();
                var pile2 = new ArrayList<Map<String, Object>>();
                if (msg.getCardsView1() != null) {
                    for (CardView card : msg.getCardsView1().values()) {
                        pile1.add(buildCardInfoMap(card));
                    }
                }
                if (msg.getCardsView2() != null) {
                    for (CardView card : msg.getCardsView2().values()) {
                        pile2.add(buildCardInfoMap(card));
                    }
                }
                result.pile1 = pile1;
                result.pile2 = pile2;
                lastChoices = null;
                break;
            }

            case GAME_GET_AMOUNT: {
                GameClientMessage msg = (GameClientMessage) data;
                result.response_type = "amount";
                result.respond_with = "amount=N (min=" + msg.getMin() + ", max=" + msg.getMax() + ")";
                result.min = msg.getMin();
                result.max = msg.getMax();
                lastChoices = null;
                break;
            }

            case GAME_GET_MULTI_AMOUNT: {
                GameClientMessage msg = (GameClientMessage) data;
                result.response_type = "multi_amount";
                result.respond_with = "amounts=[N,N,...] — one per item, sum between total_min and total_max";
                result.total_min = msg.getMin();
                result.total_max = msg.getMax();

                var items = new ArrayList<Map<String, Object>>();
                if (msg.getMessages() != null) {
                    for (MultiAmountMessage mam : msg.getMessages()) {
                        var item = new HashMap<String, Object>();
                        item.put("description", stripHtml(mam.message));
                        item.put("min", mam.min);
                        item.put("max", mam.max);
                        item.put("default", mam.defaultValue);
                        items.add(item);
                    }
                }
                result.items = items;
                // The multi-amount GameClientMessage constructor doesn't set
                // the message field; the useful context ("Assign combat damage
                // among creatures blocking X" etc.) lives in options.header
                // from MultiAmountType.
                if ((result.message == null || result.message.isEmpty())
                        && msg.getOptions() != null) {
                    Object header = msg.getOptions().get("header");
                    if (header instanceof String) {
                        result.message = stripHtml((String) header);
                    }
                }
                lastChoices = null;
                break;
            }

            default:
                result.response_type = "unknown";
                result.error = "Unhandled action type: " + method;
                lastChoices = null;
        }

        String responseType = result.response_type;
        if (responseType != null) {
            int choiceCount = -1;
            if (result.choices != null) {
                choiceCount = result.choices.size();
            }
            recordChoiceSnapshot(method.name(), responseType, choiceCount);
        } else {
            clearChoiceSnapshot();
        }

        return result;
    }

    private boolean clearPendingActionIfCurrent(PendingAction action) {
        synchronized (actionLock) {
            if (pendingAction == action) {
                pendingAction = null;
                return true;
            }
        }
        return false;
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
        if (pendingAction != action) {
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
                lastChoices = null;
                clearChoiceSnapshot();
                sendBooleanOrDie(action.gameId(), false, "auto-cancel optional GAME_TARGET");
                return NonDecisionActionStatus.AUTO_HANDLED;
            }
            return pendingAction != action
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
            lastChoices = null;
            clearChoiceSnapshot();
            sendUuidOrDie(action.gameId(), onlyTarget, "auto-select single required GAME_TARGET");
            return NonDecisionActionStatus.AUTO_HANDLED;
        }
        return pendingAction != action
            ? NonDecisionActionStatus.CHANGED
            : NonDecisionActionStatus.NOT_HANDLED;
    }

    private NonDecisionActionStatus maybeAutoHandlePendingManaAction(PendingAction action, String source) {
        if (!clearPendingActionIfCurrent(action)) {
            return pendingAction != action
                ? NonDecisionActionStatus.CHANGED
                : NonDecisionActionStatus.NOT_HANDLED;
        }

        try {
            lastChoices = null;
            clearChoiceSnapshot();
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

        synchronized (actionLock) {
            if (pendingAction == null) {
                pendingAction = action;
            }
        }
        return pendingAction != action
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
                lastChoices = null;
                clearChoiceSnapshot();
                sendUuidOrDie(action.gameId(), null, "auto GAME_CHOOSE_ABILITY null_choice");
                return NonDecisionActionStatus.AUTO_HANDLED;
            }
            return pendingAction != action
                ? NonDecisionActionStatus.CHANGED
                : NonDecisionActionStatus.NOT_HANDLED;
        }

        // Mana plan active: consume ability index and select
        if (manaPlan != null) {
            if (clearPendingActionIfCurrent(action)) {
                Integer abilityIdx = manaPlanAbilityIndex;
                manaPlanAbilityIndex = null;  // consume
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
                        manaPlan = null;
                        synchronized (unseenChat) {
                            unseenChat.add("[System] Spell cancelled — mana plan ability index was incorrect.");
                        }
                        logBridgeEvent("SPELL_CANCELLED", "mana plan ability index out of range");
                        lastChoices = null;
                        clearChoiceSnapshot();
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
                lastChoices = null;
                clearChoiceSnapshot();
                sendUuidOrDie(action.gameId(), selected,
                    "auto GAME_CHOOSE_ABILITY mana_plan");
                return NonDecisionActionStatus.AUTO_HANDLED;
            }
            return pendingAction != action
                ? NonDecisionActionStatus.CHANGED
                : NonDecisionActionStatus.NOT_HANDLED;
        }

        // Has choices, no mana plan: let the LLM decide
        return NonDecisionActionStatus.NOT_HANDLED;
    }

    private void recordChoiceSnapshot(String actionType, String responseType, int choiceCount) {
        lastChoicesActionType = actionType;
        lastChoicesResponseType = responseType;
        lastChoicesCount = choiceCount;
        lastChoicesGeneratedAtMs = System.currentTimeMillis();
    }

    private void clearChoiceSnapshot() {
        lastChoicesActionType = null;
        lastChoicesResponseType = null;
        lastChoicesCount = -1;
        lastChoicesGeneratedAtMs = 0;
    }

    private void logChoiceOutOfRangeDiagnostic(ClientCallbackMethod method, Integer index, List<Object> choices) {
        long ageMs = lastChoicesGeneratedAtMs == 0 ? -1 : System.currentTimeMillis() - lastChoicesGeneratedAtMs;
        PendingAction nowPending = pendingAction;
        String nowPendingType = nowPending == null ? "none" : nowPending.method().name();
        logger.warn("[" + client.getUsername() + "] choose_action out-of-range diagnostic: "
                + "method=" + method.name()
                + ", index=" + index
                + ", choices_size=" + (choices == null ? -1 : choices.size())
                + ", pending_now=" + nowPendingType
                + ", last_choices_action=" + (lastChoicesActionType == null ? "none" : lastChoicesActionType)
                + ", last_choices_response=" + (lastChoicesResponseType == null ? "none" : lastChoicesResponseType)
                + ", last_choices_count=" + lastChoicesCount
                + ", last_choices_age_ms=" + ageMs);
    }

    /**
     * When choose_action fails validation, attach the available choices to the error response
     * so the model can self-correct without a separate get_action_choices round trip.
     */
    private void attachChoicesToError(ChooseActionTool.Result errorResult) {
        try {
            ActionResult choicesResult = getActionChoices(null);
            if (choicesResult.choices != null) {
                errorResult.choices = choicesResult.choices;
            }
        } catch (ResponseDeliveryException e) {
            // Already in an error path — delivery failure means we can't attach choices,
            // but we shouldn't mask the original validation error.
            logger.warn("[" + client.getUsername() + "] attachChoicesToError: delivery failed, skipping: " + e.getMessage());
        }
    }

    /**
     * Build a standardized error response for choose_action failures.
     * Must reuse the caller's result map so the finally block can read success=false.
     */
    private ChooseActionTool.Result buildError(ChooseActionTool.Result result, String errorCode,
            String message, boolean retryable, PendingAction action, boolean attachChoices) {
        result.success = false;
        result.error = message;
        result.error_code = errorCode;
        result.retryable = retryable;
        pendingAction = action;
        if (attachChoices) {
            attachChoicesToError(result);
        }
        attachUnseenChat(result);
        return result;
    }

    private ChooseActionTool.Result buildError(ChooseActionTool.Result result, String errorCode,
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
     * Build a human-readable error message from batch combat failed entries.
     */
    private String batchFailedMessage(List<Map<String, Object>> failed) {
        var sb = new StringBuilder();
        for (var entry : failed) {
            if (sb.length() > 0) sb.append("; ");
            sb.append(entry.get("id")).append(": ").append(entry.get("reason"));
        }
        return sb.toString();
    }

    /**
     * Respond to the current pending action with a specific choice.
     * Exactly one parameter should be non-null, matching the response_type from getActionChoices().
     */
    public ChooseActionTool.Result chooseAction(Integer index, String id, Boolean answer, Integer amount, int[] amounts, Integer pile, String text, String[] manaPlanArray, Boolean autoTap, String[] attackers, String[] blockersArray) {
        BridgeChooseActionInput input = new BridgeChooseActionInput(
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
        if (input.usesBatchCombat()) {
            return processor.submit(BridgeCommand.of(() -> chooseActionBlockingImpl(input)));
        }

        BridgeChooseActionFlow flow = processor.submit(BridgeCommand.of(() -> {
            if (pendingChooseActionFlow != null) {
                return null;
            }
            return startChooseActionFlow(input);
        }));
        if (flow == null) {
            return processor.submit(BridgeCommand.of(() -> {
                var result = new ChooseActionTool.Result();
                result.success = false;
                result.error = "choose_action already pending";
                result.error_code = "choose_action_already_pending";
                result.retryable = true;
                attachUnseenChat(result);
                return result;
            }));
        }

        while (true) {
            try {
                return flow.awaitResult(200);
            } catch (TimeoutException e) {
                try {
                    processor.submit(BridgeCommand.of(() -> {
                        tickPendingChooseActionFlow(flow);
                        return null;
                    }));
                } catch (IllegalStateException ignored) {
                    return finishChooseActionAfterProcessorShutdown(flow);
                }
            } catch (InterruptedException e) {
                ChooseActionTool.Result interruptedResult;
                try {
                    interruptedResult = processor.submit(BridgeCommand.of(() -> interruptChooseActionFlow(flow)));
                } catch (IllegalStateException ignored) {
                    interruptedResult = finishChooseActionAfterProcessorShutdown(flow);
                }
                Thread.currentThread().interrupt();
                return interruptedResult;
            } catch (ExecutionException e) {
                Throwable cause = e.getCause();
                if (cause instanceof RuntimeException runtimeException) {
                    throw runtimeException;
                }
                throw new IllegalStateException("chooseAction request failed", cause);
            }
        }
    }

    private ChooseActionTool.Result chooseActionBlockingImpl(BridgeChooseActionInput input) {
        interactionsThisTurn++;
        PendingAction action = pendingAction;

        // Block until a pending action arrives (like pass_priority does).
        // The LLM may call choose_action before the next callback arrives
        // (e.g. double choose_action in one response, or calling it before
        // pass_priority).
        if (action == null) {
            action = awaitPendingAction();
            if (action == null) {
                return chooseActionFlowContext.noPendingActionResult();
            }
        }

        BridgeChooseActionStartResult startResult = applyChooseActionNow(input, action, true);
        ChooseActionTool.Result result = startResult.result();
        if (!startResult.waitForNextDecision()) {
            return result;
        }

        // After successful action, block until the next real decision arrives.
        // Transient callbacks that can be auto-resolved should not leak back to the model.
        // awaitDecisionAction() can trigger sendBooleanOrDie/sendUuidOrDie via
        // transitionToDecisionBoundary auto-resolve, so catch delivery failures here too.
        try {
            PendingAction next = awaitDecisionAction();
            if (next != null) {
                chooseActionFlowContext.finishChooseActionWithNextDecision(result, action, next);
            } else {
                chooseActionFlowContext.finishChooseActionWithoutNextDecision(result, action);
            }
        } catch (ResponseDeliveryException e) {
            result.success = false;
            result.error = e.getMessage();
            result.error_code = "response_delivery_failed";
            result.retryable = false;
            attachUnseenChat(result);
        }

        return result;
    }

    private static BridgeChooseActionStartResult chooseActionDone(ChooseActionTool.Result result) {
        return new BridgeChooseActionStartResult(result, false);
    }

    private static BridgeChooseActionStartResult chooseActionAwaitNextDecision(ChooseActionTool.Result result) {
        return new BridgeChooseActionStartResult(result, true);
    }

    private BridgeChooseActionStartResult applyChooseActionNow(
            BridgeChooseActionInput input,
            PendingAction action,
            boolean allowBatchCombat) {
        if (!allowBatchCombat && input.usesBatchCombat()) {
            throw new IllegalStateException("Batch combat choose_action must use the blocking path");
        }
        var result = new ChooseActionTool.Result();
        result.game_seq = action.gameSeq();
        // Local copies of parameters that may be nulled/reassigned during validation
        Integer resolvedIndex = input.index();
        String[] effectiveAttackers = input.attackers();
        String[] effectiveBlockers = input.blockers();
        String[] effectiveManaPlan = input.manaPlan();
        String id = input.id();
        Boolean answer = input.answer();
        Integer amount = input.amount();
        int[] amounts = input.amounts();
        Integer pile = input.pile();
        String text = input.text();
        Boolean autoTap = input.autoTap();

        // Loop detection: model has made too many interactions this turn — auto-handle
        if (interactionsThisTurn > maxInteractionsPerTurn) {
            logger.warn("[" + client.getUsername() + "] Loop detected (" + interactionsThisTurn
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
            result.warning = "Too many interactions this turn (" + interactionsThisTurn + "). Auto-passing until next turn.";
            return chooseActionDone(result);
        }

        // Batch combat: attackers
        if (allowBatchCombat && effectiveAttackers != null && effectiveAttackers.length > 0) {
            String combatType = detectCombatSelect(action);
            if ("attackers".equals(combatType)) {
                return chooseActionDone(handleBatchAttackers(effectiveAttackers, action, result));
            }
            // Not in declare_attackers — ignore the param and fall through
            logger.warn("[" + client.getUsername() + "] choose_action: ignoring attackers param (not in declare_attackers)");
            result.warning = "Ignored attackers parameter (not in declare_attackers phase)";
            effectiveAttackers = null;
        }

        // Batch combat: blockers
        if (allowBatchCombat && effectiveBlockers != null && effectiveBlockers.length > 0) {
            String combatType = detectCombatSelect(action);
            if ("blockers".equals(combatType)) {
                return chooseActionDone(handleBatchBlockers(effectiveBlockers, action, result));
            }
            // Not in declare_blockers — ignore the param and fall through
            logger.warn("[" + client.getUsername() + "] choose_action: ignoring blockers param (not in declare_blockers)");
            result.warning = "Ignored blockers parameter (not in declare_blockers phase)";
            effectiveBlockers = null;
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
            List<Object> choices = lastChoices;
            if (choices == null) {
                try {
                    getActionChoices(null);
                } catch (ResponseDeliveryException e) {
                    result.success = false;
                    result.error = e.getMessage();
                    result.error_code = "response_delivery_failed";
                    result.retryable = false;
                    attachUnseenChat(result);
                    return chooseActionDone(result);
                }
                choices = lastChoices;
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
        if (resolvedIndex != null && lastChoices == null) {
            logger.info("[" + client.getUsername() + "] choose_action: auto-populating choices (get_action_choices was not called)");
            buildActionChoices(action, null, false);
        }

        // Clear pending action only if it hasn't been overwritten by a new callback.
        // Without this CAS, a callback arriving between our read and this write would be lost.
        synchronized (actionLock) {
            if (pendingAction == action) {
                pendingAction = null;
            }
        }

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
                        List<Object> choices = lastChoices; // snapshot volatile to prevent TOCTOU race
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
                                    CopyOnWriteArrayList<ManaPlanEntry> parsedPlan;
                                    try {
                                        parsedPlan = parseManaPlan(effectiveManaPlan);
                                    } catch (IllegalArgumentException e) {
                                        return chooseActionDone(buildError(result, "invalid_mana_plan",
                                            "Invalid mana_plan: " + e.getMessage()
                                            + ". Expected: [\"p1\",\"p2:0\",\"RED\"]", true, action));
                                    }
                                    for (ManaPlanEntry entry : parsedPlan) {
                                        if ("tap".equals(entry.type()) && shortIds.tryResolve(entry.value()) == null) {
                                            return chooseActionDone(buildError(result, "invalid_mana_plan",
                                                "Mana plan references unknown permanent '" + entry.value()
                                                + "'. Check the board state for correct permanent IDs.", true, action));
                                        }
                                    }
                                    manaPlan = parsedPlan;
                                    // auto_tap controls fallback when plan runs out:
                                    // false = cancel spell, true/null = fall through to auto-tap
                                    manaPlanAutoTapFallback = !(autoTap != null && !autoTap);
                                    result.mana_plan_set = true;
                                    result.mana_plan_size = manaPlan.size();
                                } else if (autoTap != null && autoTap) {
                                    manaPlan = null;  // Explicit auto-tap mode
                                    manaPlanAbilityIndex = null;
                                    manaPlanAutoTapFallback = true;
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
                        List<Object> choices = lastChoices; // snapshot volatile to prevent TOCTOU race
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
                                UUID manaPlayerId = getManaPoolPlayerId(gameId, lastGameView);
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
                            List<Object> choices = lastChoices;
                            if (choices == null || choices.isEmpty()) {
                                logger.warn("[" + client.getUsername() + "] choose_action: answer=true for GAME_PLAY_MANA with no mana sources, auto-cancelling");
                                cancel = true;
                            }
                        }
                        if (cancel) {
                            // Mark spell as failed to prevent infinite retry loop
                            UUID payingForId = extractPayingForId(action.message());
                            if (payingForId != null) {
                                failedManaCasts.add(payingForId);
                            }
                            manaPlan = null;
                            manaPlanAbilityIndex = null;
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
                        List<Object> choices = lastChoices; // snapshot volatile to prevent TOCTOU race
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
                            List<Object> targetChoices = lastChoices;
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
                        UUID firstTarget = selectDeterministicTarget(autoTargets, lastChoices);
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
                    List<Object> abilityChoices = lastChoices; // snapshot volatile to prevent TOCTOU race
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
                    List<Object> choiceChoices = lastChoices; // snapshot volatile to prevent TOCTOU race
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
            lastChoices = null;
            if (Boolean.FALSE.equals(result.success)) {
                logger.warn("[" + client.getUsername() + "] choose_action failed: " + result.error);
            }
        }

        return chooseActionAwaitNextDecision(result);
    }

    private BridgeChooseActionFlow startChooseActionFlow(BridgeChooseActionInput input) {
        BridgeChooseActionFlow flow = new BridgeChooseActionFlow(chooseActionFlowContext, input);
        pendingChooseActionFlow = flow;
        interactionsThisTurn++;
        try {
            flow.start();
        } catch (ResponseDeliveryException e) {
            flow.finish(chooseActionDeliveryErrorResult(e.getMessage()));
        } catch (RuntimeException e) {
            if (pendingChooseActionFlow == flow) {
                pendingChooseActionFlow = null;
            }
            throw e;
        }
        if (flow.isDone() && pendingChooseActionFlow == flow) {
            pendingChooseActionFlow = null;
        }
        return flow;
    }

    private void advancePendingChooseActionFlow() {
        BridgeChooseActionFlow flow = pendingChooseActionFlow;
        if (flow == null) {
            return;
        }
        try {
            flow.advance();
        } catch (ResponseDeliveryException e) {
            flow.finish(chooseActionDeliveryErrorResult(e.getMessage()));
        }
        if (flow.isDone() && pendingChooseActionFlow == flow) {
            pendingChooseActionFlow = null;
        }
    }

    private void tickPendingChooseActionFlow(BridgeChooseActionFlow flow) {
        if (pendingChooseActionFlow != flow) {
            return;
        }
        advancePendingChooseActionFlow();
    }

    private ChooseActionTool.Result interruptChooseActionFlow(BridgeChooseActionFlow flow) {
        try {
            return flow.interrupt();
        } finally {
            if (pendingChooseActionFlow == flow) {
                pendingChooseActionFlow = null;
            }
        }
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

    private ChooseActionTool.Result finishChooseActionAfterProcessorShutdown(BridgeChooseActionFlow flow) {
        try {
            return flow.finishAfterProcessorShutdown();
        } finally {
            if (pendingChooseActionFlow == flow) {
                pendingChooseActionFlow = null;
            }
        }
    }

    private void advancePendingFlowsBeforeShutdown() {
        try {
            processor.submit(BridgeCommand.of(() -> {
                advancePendingChooseActionFlow();
                advancePendingPassPriorityFlow();
                return null;
            }));
        } catch (IllegalStateException ignored) {
            // Processor is already gone; pending callers will observe shutdown state.
        }
    }

    // ── Batch combat ──────────────────────────────────────────────────────

    /**
     * Transitional step-4 behavior: the remaining batch-combat choose_action
     * helpers still block on the
     * processor thread while waiting for later callbacks. In that state we must
     * keep pumping callback events instead of sleeping on actionLock, otherwise
     * the processor deadlocks waiting on the very callbacks it is supposed to
     * consume.
     *
     * TODO: Remove this once batch combat becomes a split-phase processor
     * request that suspends via processor-owned state and future completion
     * rather than monopolizing the processor thread.
     */
    private boolean waitForCallbackProgress(long timeoutMs) {
        if (processor.isProcessorThread()) {
            return processor.processNextCallback(timeoutMs);
        }
        synchronized (actionLock) {
            try {
                actionLock.wait(timeoutMs);
                return true;
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return false;
            }
        }
    }

    /**
     * Block indefinitely until a pending action arrives or the game ends.
     * Shared by the remaining blocking batch-combat choose_action path.
     */
    private PendingAction awaitPendingAction() {
        while (pendingAction == null) {
            if (superseded || playerDead || (activeGames.isEmpty() && gameEverStarted) || !client.isRunning()) {
                break;
            }
            if (!waitForCallbackProgress(200) && Thread.currentThread().isInterrupted()) {
                break;
            }
        }
        return pendingAction;
    }

    /**
     * Block until the next real player decision is pending.
     * Auto-resolves deterministic non-decisions (for example single-target mandatory
     * selections) instead of returning them to the model. Used by the remaining
     * blocking batch-combat choose_action path.
     */
    private PendingAction awaitDecisionAction() {
        while (true) {
            PendingAction action = awaitPendingAction();
            if (action == null) {
                return null;
            }
            DecisionBoundaryTransition transition =
                transitionToDecisionBoundary(action, "awaitDecisionAction");
            if (transition.status() == DecisionBoundaryStatus.READY) {
                return transition.action();
            }
        }
    }

    /**
     * Inspect the current pending action and auto-resolve any deterministic
     * non-decision callbacks. Unlike awaitDecisionAction(), this does not block.
     */
    private PendingAction currentDecisionAction() {
        while (true) {
            PendingAction action = pendingAction;
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

    /**
     * Wait for the next pending action callback from the server.
     * Used internally by batch combat to chain multiple send→wait cycles.
     * Returns the new PendingAction, or null on timeout.
     */
    private PendingAction waitForNextCallback() {
        long waitStart = System.currentTimeMillis();
        while (true) {
            PendingAction next = pendingAction;
            if (next != null) {
                return next;
            }
            if (superseded || playerDead || (activeGames.isEmpty() && gameEverStarted) || !client.isRunning()) {
                return null;
            }
            if (System.currentTimeMillis() - waitStart > 10_000) {
                logger.warn("[" + client.getUsername() + "] waitForNextCallback: timed out after 10s");
                return null;
            }
            if (!waitForCallbackProgress(200) && Thread.currentThread().isInterrupted()) {
                return null;
            }
        }
    }

    /**
     * Declare multiple attackers in one batch.
     * Sends each attacker UUID, waits for the next GAME_SELECT, then confirms.
     * Special case: attackers=["all"] sends the "special" all-attack button.
     */
    @SuppressWarnings("unchecked")
    private ChooseActionTool.Result handleBatchAttackers(String[] attackerIds, PendingAction action, ChooseActionTool.Result result) {
        try {
            return handleBatchAttackersBody(attackerIds, action, result);
        } catch (ResponseDeliveryException e) {
            result.success = false;
            result.error = e.getMessage();
            result.error_code = "response_delivery_failed";
            result.retryable = false;
            attachUnseenChat(result);
            return result;
        }
    }

    @SuppressWarnings("unchecked")
    private ChooseActionTool.Result handleBatchAttackersBody(String[] attackerIds, PendingAction action, ChooseActionTool.Result result) {
        UUID gameId = action.gameId();
        var declared = new ArrayList<Map<String, Object>>();
        var failed = new ArrayList<Map<String, Object>>();

        // Special case: "all" attack
        if (attackerIds.length == 1 && "all".equals(attackerIds[0])) {
            synchronized (actionLock) {
                if (pendingAction == action) {
                    pendingAction = null;
                }
            }
            sendStringOrDie(gameId, "special", "batchAttack:all");
            // Wait for next callback (server will send a new GAME_SELECT to confirm)
            PendingAction next = waitForNextCallback();
            if (next != null && next.method() == ClientCallbackMethod.GAME_SELECT) {
                synchronized (actionLock) {
                    if (pendingAction == next) {
                        pendingAction = null;
                    }
                }
                sendBooleanOrDie(gameId, true, "batchAttack:confirm_all");
            }
            result.success = true;
            result.action_taken = "batch_attack";
            declared.add(Map.of("id", "all"));
            result.declared = new ArrayList<>(declared);
            lastChoices = null;
            waitForNextActionAfterBatch(result);
            return result;
        }

        // Get possibleAttackers from the current action's options
        GameClientMessage gcm = (GameClientMessage) action.data();
        Map<String, Serializable> options = gcm.getOptions();
        List<UUID> possibleAttackerUuids = (List<UUID>) options.get("possibleAttackers");

        for (String shortId : attackerIds) {
            UUID attackerUuid;
            try {
                attackerUuid = shortIds.resolve(shortId);
            } catch (IllegalArgumentException e) {
                failed.add(Map.of("id", shortId, "reason", "unknown short ID"));
                continue;
            }

            // Verify this attacker is in the possible list
            if (possibleAttackerUuids == null || !possibleAttackerUuids.contains(attackerUuid)) {
                failed.add(Map.of("id", shortId, "reason", "not a valid attacker"));
                continue;
            }

            // Clear pending action and send the attacker UUID
            synchronized (actionLock) {
                if (pendingAction != null) {
                    pendingAction = null;
                }
            }
            sendUuidOrDie(gameId, attackerUuid, "batchAttack:declare_attacker");
            declared.add(Map.of("id", shortId));

            // Wait for next callback
            PendingAction next = waitForNextCallback();
            if (next == null) {
                result.interrupted = true;
                break;
            }
            if (next.method() != ClientCallbackMethod.GAME_SELECT) {
                // Interrupted by a trigger or other callback
                result.interrupted = true;
                break;
            }
            // Update possibleAttackers from the new callback for validation
            if (next.data() instanceof GameClientMessage nextGcm) {
                Map<String, Serializable> nextOptions = nextGcm.getOptions();
                if (nextOptions != null && nextOptions.containsKey("possibleAttackers")) {
                    possibleAttackerUuids = (List<UUID>) nextOptions.get("possibleAttackers");
                }
            }
        }

        // Confirm attackers (send true)
        if (!Boolean.TRUE.equals(result.interrupted)) {
            synchronized (actionLock) {
                if (pendingAction != null) {
                    pendingAction = null;
                }
            }
            sendBooleanOrDie(gameId, true, "batchAttack:confirm");
        }

        result.success = !Boolean.TRUE.equals(result.interrupted) && failed.isEmpty();
        result.action_taken = "batch_attack";
        result.declared = new ArrayList<>(declared);
        if (!failed.isEmpty()) {
            result.failed = new ArrayList<>(failed);
            result.error = batchFailedMessage(failed);
            result.error_code = "batch_failed";
            result.retryable = true;
        }
        lastChoices = null;
        waitForNextActionAfterBatch(result);
        return result;
    }

    /**
     * Declare multiple blockers in one batch.
     * Format: [{"id":"p5","blocks":"p1"},{"id":"p6","blocks":"p2"}]
     * Sends each blocker UUID, then the attacker UUID when prompted, then confirms.
     */
    @SuppressWarnings("unchecked")
    private ChooseActionTool.Result handleBatchBlockers(String[] blockersArray, PendingAction action, ChooseActionTool.Result result) {
        try {
            return handleBatchBlockersBody(blockersArray, action, result);
        } catch (ResponseDeliveryException e) {
            result.success = false;
            result.error = e.getMessage();
            result.error_code = "response_delivery_failed";
            result.retryable = false;
            attachUnseenChat(result);
            return result;
        }
    }

    @SuppressWarnings("unchecked")
    private ChooseActionTool.Result handleBatchBlockersBody(String[] blockersArray, PendingAction action, ChooseActionTool.Result result) {
        UUID gameId = action.gameId();
        var declared = new ArrayList<Map<String, Object>>();
        var failed = new ArrayList<Map<String, Object>>();

        // Parse blocker assignments
        // Expected: ["p5:p1","p6:p2"] (blocker_id:attacker_id)
        List<Map<String, String>> assignments;
        try {
            assignments = parseBlockerAssignments(blockersArray);
        } catch (IllegalArgumentException e) {
            return buildError(result, "invalid_blockers",
                "Invalid blockers: " + e.getMessage()
                + ". Expected: [\"blocker:attacker\",...]", false, action);
        }

        // Get possibleBlockers from the current action's options
        GameClientMessage gcm = (GameClientMessage) action.data();
        Map<String, Serializable> options = gcm.getOptions();
        List<UUID> possibleBlockerUuids = (List<UUID>) options.get("possibleBlockers");

        for (Map<String, String> assignment : assignments) {
            String blockerShortId = assignment.get("id");
            String attackerShortId = assignment.get("blocks");

            UUID blockerUuid;
            try {
                blockerUuid = shortIds.resolve(blockerShortId);
            } catch (IllegalArgumentException e) {
                failed.add(Map.of("id", blockerShortId, "reason", "unknown short ID"));
                continue;
            }

            // Verify this blocker is in the possible list
            if (possibleBlockerUuids == null || !possibleBlockerUuids.contains(blockerUuid)) {
                failed.add(Map.of("id", blockerShortId, "reason", "not a valid blocker"));
                continue;
            }

            // Clear pending action and send the blocker UUID
            synchronized (actionLock) {
                if (pendingAction != null) {
                    pendingAction = null;
                }
            }
            sendUuidOrDie(gameId, blockerUuid, "batchBlock:declare_blocker");

            // Wait for next callback — could be GAME_TARGET (pick which attacker)
            // or GAME_SELECT (single attacker, auto-assigned)
            PendingAction next = waitForNextCallback();
            if (next == null) {
                result.interrupted = true;
                break;
            }

            if (next.method() == ClientCallbackMethod.GAME_TARGET) {
                // Multiple attackers — server asks which one to block
                UUID attackerUuid;
                try {
                    attackerUuid = shortIds.resolve(attackerShortId);
                } catch (IllegalArgumentException e) {
                    failed.add(Map.of("id", blockerShortId, "reason", "unknown attacker ID: " + attackerShortId));
                    // Cancel the target selection
                    synchronized (actionLock) {
                        if (pendingAction == next) {
                            pendingAction = null;
                        }
                    }
                    sendBooleanOrDie(gameId, false, "batchBlock:cancel_unknown_attacker");
                    next = waitForNextCallback();
                    if (next == null || next.method() != ClientCallbackMethod.GAME_SELECT) {
                        result.interrupted = true;
                        break;
                    }
                    continue;
                }

                // Verify the attacker UUID is a valid target
                GameClientMessage targetMsg = (GameClientMessage) next.data();
                Set<UUID> validTargets = findValidTargets(targetMsg);
                if (validTargets == null || !validTargets.contains(attackerUuid)) {
                    failed.add(Map.of("id", blockerShortId, "reason",
                        "attacker " + attackerShortId + " is not a valid block target"));
                    synchronized (actionLock) {
                        if (pendingAction == next) {
                            pendingAction = null;
                        }
                    }
                    sendBooleanOrDie(gameId, false, "batchBlock:cancel_invalid_target");
                    next = waitForNextCallback();
                    if (next == null || next.method() != ClientCallbackMethod.GAME_SELECT) {
                        result.interrupted = true;
                        break;
                    }
                    continue;
                }

                // Send the attacker UUID as the target
                synchronized (actionLock) {
                    if (pendingAction == next) {
                        pendingAction = null;
                    }
                }
                sendUuidOrDie(gameId, attackerUuid, "batchBlock:select_attacker");
                declared.add(Map.of("id", blockerShortId, "blocks", attackerShortId));

                // Wait for next GAME_SELECT (back to blocker selection)
                next = waitForNextCallback();
                if (next == null || next.method() != ClientCallbackMethod.GAME_SELECT) {
                    result.interrupted = true;
                    break;
                }

                // Update possibleBlockers from the new callback
                if (next.data() instanceof GameClientMessage nextGcm) {
                    Map<String, Serializable> nextOptions = nextGcm.getOptions();
                    if (nextOptions != null && nextOptions.containsKey("possibleBlockers")) {
                        possibleBlockerUuids = (List<UUID>) nextOptions.get("possibleBlockers");
                    }
                }
            } else if (next.method() == ClientCallbackMethod.GAME_SELECT) {
                // Single attacker — auto-assigned by the server (lines 3010-3024 in handleCallback)
                declared.add(Map.of("id", blockerShortId, "blocks", attackerShortId));

                // Update possibleBlockers from the new callback
                if (next.data() instanceof GameClientMessage nextGcm2) {
                    Map<String, Serializable> nextOptions = nextGcm2.getOptions();
                    if (nextOptions != null && nextOptions.containsKey("possibleBlockers")) {
                        possibleBlockerUuids = (List<UUID>) nextOptions.get("possibleBlockers");
                    }
                }
            } else {
                // Interrupted by unexpected callback
                result.interrupted = true;
                break;
            }
        }

        // Confirm blockers (send true)
        if (!Boolean.TRUE.equals(result.interrupted)) {
            synchronized (actionLock) {
                if (pendingAction != null) {
                    pendingAction = null;
                }
            }
            sendBooleanOrDie(gameId, true, "batchBlock:confirm");
        }

        result.success = !Boolean.TRUE.equals(result.interrupted) && failed.isEmpty();
        result.action_taken = "batch_block";
        result.declared = new ArrayList<>(declared);
        if (!failed.isEmpty()) {
            result.failed = new ArrayList<>(failed);
            result.error = batchFailedMessage(failed);
            result.error_code = "batch_failed";
            result.retryable = true;
        }
        lastChoices = null;
        waitForNextActionAfterBatch(result);
        return result;
    }

    /**
     * Parse blocker assignments: ["p5:p1","p6:p2"] where each entry is "blocker_id:attacker_id".
     */
    private List<Map<String, String>> parseBlockerAssignments(String[] arr) {
        var assignments = new ArrayList<Map<String, String>>();
        for (int i = 0; i < arr.length; i++) {
            String entry = arr[i];
            int colonIdx = entry.indexOf(':');
            if (colonIdx < 0) {
                throw new IllegalArgumentException("blockers entry " + i + " must be \"blocker:attacker\", got: " + entry);
            }
            String blockerId = entry.substring(0, colonIdx);
            String attackerId = entry.substring(colonIdx + 1);
            if (blockerId.isEmpty() || attackerId.isEmpty()) {
                throw new IllegalArgumentException("blockers entry " + i + " has empty id in: " + entry);
            }
            assignments.add(Map.of("id", blockerId, "blocks", attackerId));
        }
        return assignments;
    }

    /**
     * After batch combat, block until the next pending action arrives.
     * Populates full ActionResult fields via mergeActionChoices.
     */
    private void waitForNextActionAfterBatch(ChooseActionTool.Result result) {
        PendingAction next = awaitDecisionAction();
        if (next != null) {
            result.game_seq = next.gameSeq();
            mergeActionChoices(result, null, next);
        } else {
            attachUnseenChat(result);
        }
    }

    // ── End batch combat ──────────────────────────────────────────────────

    /** Populate target info and return the resolved CardView (null if target is a player or unknown). */
    private CardView buildTargetInfo(Map<String, Object> entry, UUID targetId,
                                  CardsView cardsView, GameView gameView, UUID myPlayerId) {
        return cardFormatter.buildTargetInfo(entry, targetId, cardsView, gameView, myPlayerId);
    }

    private List<Map<String, Object>> buildStackItems(GameView gameView, boolean includeIds, boolean includeRules) {
        return cardFormatter.buildStackItems(gameView, includeIds, includeRules);
    }

    private String safeDisplayName(CardView cv) {
        return cardFormatter.safeDisplayName(cv);
    }

    /**
     * Build a structured info map for a card: name, mana_cost, is_land, power/toughness, rules.
     * Used for hand cards, pile decisions, and mulligan hands.
     */
    private Map<String, Object> buildCardInfoMap(CardView cv) {
        return cardFormatter.buildCardInfoMap(cv);
    }

    public GetGameLogTool.Result getGameLogChunk(int maxChars, Integer cursor) {
        GameLogSnapshot snapshot = snapshotGameLog();
        List<BridgeLogEntry> allEvents = snapshot.events();

        if (cursor != null) {
            // Incremental: render only events from the cursor onward.
            // Skip full-log render — total_length is not set for deltas since
            // it would require an O(history) render just for an informational field.
            // Exclude chat — cursor-based deltas don't track chat position,
            // and chat is already surfaced via recent_chat in decision prompts.
            final int c = cursor;
            List<BridgeLogEntry> responseEvents = allEvents.stream()
                    .filter(e -> e.index() >= c)
                    .toList();

            // Pre-populate turn counts from events before the cursor so turn
            // headers in the slice use absolute per-player turn numbers.
            Map<String, Integer> priorTurns = new HashMap<>();
            for (BridgeLogEntry e : allEvents) {
                if (e.index() >= c) break;
                if ("BEGIN_TURN".equals(e.type())) {
                    priorTurns.merge(e.activePlayer(), 1, Integer::sum);
                }
            }

            String rendered = renderGameLogFlat(responseEvents, priorTurns, c, false);
            GetGameLogTool.Result result = buildGameLogResult(snapshot, rendered, null, maxChars);

            if (!responseEvents.isEmpty() && responseEvents.get(0).index() > cursor) {
                result.cursor_reset = true;
            }
            return result;
        } else {
            // Full log: render all events with chat
            String rendered = renderGameLogFlat(allEvents, Map.of(), 0, true);
            return buildGameLogResult(snapshot, rendered, rendered.length(), maxChars);
        }
    }

    /**
     * Return game log entries starting from a specific player's Nth per-player turn.
     * Computes per-player turn numbers from BEGIN_TURN bridge events at read time.
     * If player is null, defaults to this client's player name.
     */
    public GetGameLogTool.Result getGameLogSinceTurn(String player, int sinceTurn) {
        String effectivePlayer = player != null ? player : client.getUsername();
        GameLogSnapshot snapshot = snapshotGameLog();
        List<BridgeLogEntry> allEvents = snapshot.events();
        Map<String, Integer> emptyTurns = Map.of();

        String allRendered = renderGameLogFlat(allEvents, emptyTurns, 0, true);

        // Find the event index where the player's Nth per-player turn starts,
        // and collect turn counts for all players up to that point so the
        // rendered slice uses correct absolute turn numbers.
        Map<String, Integer> priorTurns = new HashMap<>();
        int startIdx = -1;
        for (int i = 0; i < allEvents.size(); i++) {
            BridgeLogEntry e = allEvents.get(i);
            if ("BEGIN_TURN".equals(e.type())) {
                int count = priorTurns.merge(e.activePlayer(), 1, Integer::sum);
                if (effectivePlayer.equals(e.activePlayer()) && count == sinceTurn) {
                    // Found the target turn — priorTurns already includes this turn's
                    // count, but renderGameLogFlat will re-count this BEGIN_TURN event,
                    // so subtract 1 to avoid double-counting.
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
        } else {
            // Count total per-player turns to distinguish "trimmed" vs "hasn't happened"
            int totalPlayerTurns = priorTurns.getOrDefault(effectivePlayer, 0);
            if (totalPlayerTurns > 0 && sinceTurn <= totalPlayerTurns) {
                GetGameLogTool.Result result = buildGameLogResult(snapshot, allRendered, allRendered.length(), null);
                result.truncated = true;
                result.since_player = effectivePlayer;
                return result;
            } else {
                GetGameLogTool.Result result = buildGameLogResult(snapshot, "", allRendered.length(), null);
                result.truncated = false;
                return result;
            }
        }
    }

    private GameLogSnapshot snapshotGameLog() {
        pullBridgeEvents();
        List<BridgeLogEntry> allEvents = new ArrayList<>(cachedBridgeEvents);
        return new GameLogSnapshot(allEvents, nextBridgeEventCursor(allEvents));
    }

    private List<BridgeChatLogEntry> snapshotChatLog() {
        synchronized (chatLog) {
            return new ArrayList<>(chatLog);
        }
    }

    private static int nextBridgeEventCursor(List<BridgeLogEntry> allEvents) {
        return allEvents.isEmpty() ? 0 : allEvents.get(allEvents.size() - 1).index() + 1;
    }

    private static GetGameLogTool.Result buildGameLogResult(
            GameLogSnapshot snapshot,
            String rendered,
            Integer totalLength,
            Integer maxChars) {
        return BridgeGameLogFormatter.buildGameLogResult(snapshot.cursor(), rendered, totalLength, maxChars);
    }

    /**
     * Pull new bridge events from the server since our last cursor.
     * Returns the list of new events and advances the cursor.
     */
    private List<BridgeLogEntry> pullBridgeEvents() {
        UUID gameId = currentGameId;
        if (gameId == null) return List.of();
        UUID playerId = playerIdForGame(gameId);
        if (playerId == null) return List.of();
        try {
            List<BridgeLogEntry> events = session.getBridgeEvents(gameId, playerId, bridgeEventCursor);
            if (events != null && !events.isEmpty()) {
                bridgeEventCursor = events.get(events.size() - 1).index() + 1;
                // Only append events not already in the cache. getGameHistory() temporarily
                // rewinds bridgeEventCursor (save/restore pattern), which can re-fetch events
                // that were already cached from a prior pull.
                int cacheHighWater = cachedBridgeEvents.isEmpty() ? -1
                        : cachedBridgeEvents.get(cachedBridgeEvents.size() - 1).index();
                for (BridgeLogEntry e : events) {
                    if (e.index() > cacheHighWater) {
                        cachedBridgeEvents.add(e);
                    }
                }
            }
            return events != null ? events : List.of();
        } catch (Exception e) {
            logger.error("[" + client.getUsername() + "] Failed to pull bridge events", e);
            return List.of();
        }
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
        // Fetch events directly from the server without going through pullBridgeEvents,
        // which would pollute cachedBridgeEvents with out-of-order entries when sinceCursor
        // rewinds the fetch window (e.g. sinceCursor=0 after a prior pull from cursor=50).
        int effectiveCursor = (sinceCursor != null) ? sinceCursor : 0;
        List<BridgeLogEntry> events = List.of();
        int newCursor = effectiveCursor;
        UUID gameId = currentGameId;
        UUID playerId = gameId != null ? playerIdForGame(gameId) : null;
        if (gameId != null && playerId != null) {
            try {
                List<BridgeLogEntry> fetched = session.getBridgeEvents(gameId, playerId, effectiveCursor);
                if (fetched != null && !fetched.isEmpty()) {
                    events = fetched;
                    newCursor = fetched.get(fetched.size() - 1).index() + 1;
                    // Merge into cache for post-game fallback, using high-water dedup
                    // to avoid duplicates without polluting order.
                    int cacheHighWater = cachedBridgeEvents.isEmpty() ? -1
                            : cachedBridgeEvents.get(cachedBridgeEvents.size() - 1).index();
                    for (BridgeLogEntry e : fetched) {
                        if (e.index() > cacheHighWater) {
                            cachedBridgeEvents.add(e);
                        }
                    }
                }
            } catch (Exception e) {
                logger.error("[" + client.getUsername() + "] Failed to fetch bridge events for history", e);
            }
        }

        // If the server returned nothing (game ended, controller cleaned up),
        // fall back to cached events from earlier pulls.
        if (events.isEmpty() && !cachedBridgeEvents.isEmpty()) {
            if (sinceCursor != null) {
                events = cachedBridgeEvents.stream()
                        .filter(e -> e.index() >= sinceCursor)
                        .toList();
            } else {
                events = new ArrayList<>(cachedBridgeEvents);
            }
            newCursor = cachedBridgeEvents.isEmpty() ? 0
                    : cachedBridgeEvents.get(cachedBridgeEvents.size() - 1).index() + 1;
        }

        // Filter by sinceTurn if specified
        if (sinceTurn != null) {
            events = events.stream()
                    .filter(e -> e.turn() >= sinceTurn)
                    .toList();
        }
        return BridgeGameLogFormatter.buildGameHistoryResult(events, newCursor);
    }

    /**
     * Render bridge events as flat text with per-player turn headers, interleaved
     * with chat messages captured during gameplay. Used by GetGameLogTool.
     * Distinct from getGameHistory() which uses phase sub-headers and global turn numbers.
     *
     * @param events the bridge events to render
     * @param initialTurnCounts pre-populated per-player turn counts (for rendering slices
     *        with correct absolute turn numbers); empty map starts from turn 1
     * @param minChatCursor only include chat entries with eventCursor >= this value
     *        (prevents replaying old chat on incremental cursor-based calls)
     * @param includeChat whether to interleave chat messages; false for cursor-based
     *        deltas where chat is already surfaced via recent_chat in decisions
     */
    private String renderGameLogFlat(List<BridgeLogEntry> events,
                                     Map<String, Integer> initialTurnCounts,
                                     int minChatCursor,
                                     boolean includeChat) {
        return BridgeGameLogFormatter.renderGameLogFlat(
            events,
            snapshotChatLog(),
            initialTurnCounts,
            minChatCursor,
            includeChat
        );
    }

    /**
     * Send a chat message. Returns null on success, or an error string on failure.
     */
    public String sendChatMessage(String message) {
        return processor.submit(BridgeCommand.of(() -> sendChatMessageImpl(message)));
    }

    private String sendChatMessageImpl(String message) {
        UUID gameId = currentGameId;
        if (gameId == null) {
            logger.warn("[" + client.getUsername() + "] Cannot send chat: no active game");
            return "no active game";
        }
        UUID chatId = gameChatIds.get(gameId);
        if (chatId == null) {
            logger.warn("[" + client.getUsername() + "] Cannot send chat: no chat ID for game " + gameId);
            return "no chat session for this game";
        }
        // Suppress duplicate messages within the dedup window
        long now = System.currentTimeMillis();
        if (message.equals(lastChatMessage) && (now - lastChatTimeMs) < CHAT_DEDUP_WINDOW_MS) {
            logger.info("[" + client.getUsername() + "] Suppressing duplicate chat message");
            return null; // Pretend success so the model doesn't retry
        }
        lastChatMessage = message;
        lastChatTimeMs = now;
        if (!session.sendChatMessage(chatId, message)) {
            return "server rejected the message";
        }
        return null;
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
        UUID gameId = currentGameId;
        if (gameId == null) {
            logger.warn("[" + client.getUsername() + "] Cannot concede: no active game");
            return false;
        }
        if (!activeGames.containsKey(gameId)) {
            // Game already ended (e.g. opponent conceded first) — the XMage
            // session is disconnected, so sending CONCEDE would fail.
            logger.info("[" + client.getUsername() + "] Game already over, concede is a no-op");
            return true;
        }
        logger.info("[" + client.getUsername() + "] Conceding game " + gameId);
        session.sendPlayerAction(PlayerAction.CONCEDE, gameId, null);
        // In keepAlive mode, wait for the server to end the game before returning.
        // handleGameOver fires gameFinishedLatch when the server confirms the game ended.
        if (keepAliveAfterGame) {
            try {
                boolean finished = gameFinishedLatch.await(
                    KEEPALIVE_CONCEDE_WAIT_SECONDS,
                    java.util.concurrent.TimeUnit.SECONDS
                );
                if (!finished) {
                    logger.warn(
                        "[" + client.getUsername() + "] Concede sent but GAME_OVER not received within "
                            + KEEPALIVE_CONCEDE_WAIT_SECONDS + "s"
                    );
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
        return true;
    }

    /**
     * Drain unseen chat messages and attach to result map (if any).
     */
    private void attachUnseenChat(Map<String, Object> result) {
        if (playerDead) {
            result.put("player_dead", true);
        }
        if (activeGames.isEmpty() && gameEverStarted) {
            result.put("game_over", true);
        }
        synchronized (unseenChat) {
            if (!unseenChat.isEmpty()) {
                result.put("recent_chat", new ArrayList<>(unseenChat));
                unseenChat.clear();
            }
        }
    }

    private void attachUnseenChat(ActionResult result) {
        if (playerDead) result.player_dead = true;
        if (activeGames.isEmpty() && gameEverStarted) result.game_over = true;
        synchronized (unseenChat) {
            if (!unseenChat.isEmpty()) {
                result.recent_chat = new ArrayList<>(unseenChat);
                unseenChat.clear();
            }
        }
    }

    private void mergeActionChoices(ActionResult result, Long boardCursorParam, PendingAction action) {
        ActionResult choices = buildActionChoices(action, boardCursorParam, false);
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

    private ActionResult pendingActionResult(
            PendingAction action,
            String stopReason,
            Long boardCursorParam
    ) {
        return pendingActionResult(action, stopReason, boardCursorParam, null);
    }

    private ActionResult pendingActionResult(
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

    private ActionResult stackResolvedResult(PendingAction action, Long boardCursorParam) {
        return pendingActionResult(action, "stack_resolved", boardCursorParam);
    }

    private ActionResult stepYieldResult(PendingAction action, GameView gv, String stopReason, Long boardCursorParam) {
        return pendingActionResult(action, stopReason, boardCursorParam, result -> {
            if (gv != null && gv.getStep() != null) {
                result.current_step = gv.getStep().toString();
            }
        });
    }

    private UUID lowestStackObjectId(GameView gameView) {
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

    private boolean stackContains(GameView gameView, UUID stackObjectId) {
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
        BridgePassPriorityFlow flow = processor.submit(BridgeCommand.of(() -> {
            if (pendingPassPriorityFlow != null) {
                return null;
            }
            return startPassPriorityFlow(until, boardCursorParam);
        }));

        if (flow == null) {
            return processor.submit(BridgeCommand.of(() -> {
                var result = new ActionResult();
                result.error = "pass_priority already pending";
                attachUnseenChat(result);
                return result;
            }));
        }

        while (true) {
            try {
                return flow.awaitResult(200);
            } catch (TimeoutException e) {
                processor.submit(BridgeCommand.of(() -> {
                    tickPendingPassPriorityFlow(flow);
                    return null;
                }));
            } catch (InterruptedException e) {
                ActionResult interruptedResult;
                try {
                    interruptedResult = processor.submit(BridgeCommand.of(() -> interruptPassPriorityFlow(flow)));
                } catch (IllegalStateException ignored) {
                    interruptedResult = interruptPassPriorityFlow(flow);
                }
                Thread.currentThread().interrupt();
                return interruptedResult;
            } catch (ExecutionException e) {
                Throwable cause = e.getCause();
                if (cause instanceof RuntimeException runtimeException) {
                    throw runtimeException;
                }
                throw new IllegalStateException("passPriority request failed", cause);
            }
        }
    }

    private BridgePassPriorityFlow startPassPriorityFlow(String until, Long boardCursorParam) {
        BridgePassPriorityFlow flow = new BridgePassPriorityFlow(passPriorityFlowContext, until, boardCursorParam);
        pendingPassPriorityFlow = flow;
        interactionsThisTurn++;
        try {
            flow.start();
        } catch (ResponseDeliveryException e) {
            flow.finishWithDeliveryError(e.getMessage());
        } catch (RuntimeException e) {
            if (pendingPassPriorityFlow == flow) {
                pendingPassPriorityFlow = null;
            }
            throw e;
        }
        return flow;
    }

    private void advancePendingPassPriorityFlow() {
        BridgePassPriorityFlow flow = pendingPassPriorityFlow;
        if (flow == null) {
            return;
        }
        try {
            flow.advance();
        } catch (ResponseDeliveryException e) {
            flow.finishWithDeliveryError(e.getMessage());
        }
    }

    private void tickPendingPassPriorityFlow(BridgePassPriorityFlow flow) {
        if (pendingPassPriorityFlow != flow) {
            return;
        }
        try {
            flow.tick();
        } catch (ResponseDeliveryException e) {
            flow.finishWithDeliveryError(e.getMessage());
        }
    }

    private ActionResult interruptPassPriorityFlow(BridgePassPriorityFlow flow) {
        return flow.interrupt();
    }

    /**
     * Combined helper for models: wait using pass_priority, then return full choices.
     * pass_priority already merges action choices, so this is just a pass-through.
     */
    public ActionResult waitAndGetChoices(String until, Long boardCursorParam) {
        return passPriority(until, boardCursorParam);
    }


    public GetGameStateTool.Result getGameState(Long cursor) {
        return processor.submit(BridgeCommand.of(() -> getGameStateWithCursorImpl(cursor)));
    }

    private GetGameStateTool.Result getGameStateWithCursorImpl(Long cursor) {
        GetGameStateTool.Result fullState = getGameState();
        if (!Boolean.TRUE.equals(fullState.available)) {
            return fullState;
        }
        long currentCursor = updateGameStateCursor(McpToolRegistry.resultToMap(fullState));
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

    public GetGameStateTool.Result getGameState() {
        return processor.submit(BridgeCommand.of(this::getGameStateImpl));
    }

    private GetGameStateTool.Result getGameStateImpl() {
        var state = new GetGameStateTool.Result();
        GameView gameView = lastGameView;
        if (gameView == null) {
            state.available = false;
            state.error = "No game state available yet";
            return state;
        }

        state.available = true;
        state.game_seq = gameView.getGameSeq();
        // Determinism debugging: log what game_seq getGameState returns
        {
            String step = gameView.getStep() != null ? gameView.getStep().toString() : "null";
            logger.debug("[" + client.getUsername() + "] getGameState returning game_seq="
                + gameView.getGameSeq() + " step=" + step
                + " thread=" + Thread.currentThread().getName());
        }
        state.turn = roundTracker.update(gameView);

        // Phase info
        if (gameView.getPhase() != null) {
            state.phase = gameView.getPhase().toString();
        }
        if (gameView.getStep() != null) {
            state.step = gameView.getStep().toString();
        }

        state.active_player = gameView.getActivePlayerName();
        state.priority_player = gameView.getPriorityPlayerName();

        // Players
        state.players = buildPlayersArray(gameView);

        // Stack
        List<Map<String, Object>> stack = buildStackItems(gameView, true, true);
        state.stack = stack;

        // Combat
        List<Map<String, Object>> combatGroups = buildCombatGroups(gameView);
        if (combatGroups != null) {
            state.combat = combatGroups;
        }

        return state;
    }

    /**
     * Build the full players array with board state. Includes hand (ours only),
     * battlefield (with rules), graveyard, exile, mana pool, counters, commanders.
     * Shared by getGameState() and getActionChoices().
     */
    private List<Map<String, Object>> buildPlayersArray(GameView gameView) {
        return gameStateBuilder.buildPlayersArray(gameView);
    }

    /**
     * Build combat group info from the game view. Returns null if no combat.
     * Shared by getActionChoices() and getGameState().
     */
    private List<Map<String, Object>> buildCombatGroups(GameView gameView) {
        return gameStateBuilder.buildCombatGroups(gameView);
    }

    private long updateGameStateCursor(Map<String, Object> state) {
        String signature = BridgeGameStateBuilder.buildStateSignature(state);
        synchronized (stateCursorLock) {
            if (lastGameStateSignature == null || !lastGameStateSignature.equals(signature)) {
                gameStateCursor++;
                lastGameStateSignature = signature;
            }
            return gameStateCursor;
        }
    }

    private long updateBoardCursor(List<Map<String, Object>> players) {
        String signature = BridgeGameStateBuilder.buildStateSignature(players);
        synchronized (boardCursorLock) {
            if (lastBoardSignature == null || !lastBoardSignature.equals(signature)) {
                boardCursor++;
                lastBoardSignature = signature;
            }
            return boardCursor;
        }
    }

    public Map<String, Object> getMyDecklist() {
        var result = new HashMap<String, Object>();
        DeckCardLists deck = this.deckList;
        if (deck == null) {
            result.put("error", "No deck loaded");
            return result;
        }

        var cards = new StringBuilder();
        for (DeckCardInfo card : deck.getCards()) {
            if (cards.length() > 0) cards.append("\n");
            cards.append(card.getAmount()).append("x ").append(card.getCardName());
        }
        result.put("cards", cards.toString());

        if (!deck.getSideboard().isEmpty()) {
            var sb = new StringBuilder();
            for (DeckCardInfo card : deck.getSideboard()) {
                if (sb.length() > 0) sb.append("\n");
                sb.append(card.getAmount()).append("x ").append(card.getCardName());
            }
            result.put("sideboard", sb.toString());
        }

        return result;
    }

    /**
     * Collect all creature subtypes from cards in the deck.
     * Used to filter large GAME_CHOOSE_CHOICE lists (e.g. Herald's Horn).
     */
    private Set<String> getDeckCreatureTypes() {
        var types = new HashSet<String>();
        DeckCardLists deck = this.deckList;
        if (deck == null) return types;
        for (DeckCardInfo card : deck.getCards()) {
            CardInfo info = CardRepository.instance.findCard(card.getCardName());
            if (info != null) {
                for (SubType st : info.getSubTypes()) {
                    if (st.getSubTypeSet() == SubTypeSet.CreatureType) {
                        types.add(st.toString());
                    }
                }
            }
        }
        return types;
    }

    public GetOracleTextTool.Result getOracleText(String cardName, String objectId, String[] cardNames, String[] objectIds) {
        return oracleTextService.getOracleText(cardName, objectId, cardNames, objectIds);
    }

    private String getStableShortId(UUID objectId, CardView cardView) {
        return viewLocator.getStableShortId(objectId, cardView);
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
    private String detectCombatSelect(PendingAction action) {
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

    /**
     * Look up a PermanentView by UUID from all players' battlefields.
     */
    private PermanentView findPermanentViewById(UUID objectId, GameView gameView) {
        return viewLocator.findPermanentViewById(objectId, gameView);
    }

    public void handleCallback(ClientCallback callback) {
        ClientCallbackMethod method = callback.getMethod();
        try {
            callback.decompressData();
            processor.enqueueCallback(new BridgeCallbackEvent(
                callback.getObjectId(),
                method,
                callback.getData()
            ));
        } catch (Exception e) {
            handleCallbackException(method, e, ACTIONABLE_CALLBACKS.contains(method));
        }
    }

    private void recordCallbackArrival(ClientCallbackMethod method) {
        long now = System.currentTimeMillis();
        lastCallbackReceivedAt = now;
        if (ACTIONABLE_CALLBACKS.contains(method)) {
            lastActionableCallbackAt = now;
        }
    }

    private void handleCallbackException(ClientCallbackMethod method, Exception e, boolean actionable) {
        logError("Error handling callback " + method + ": " + e.getMessage());
        logger.debug("[" + client.getUsername() + "] Callback error stack trace", e);
        // If an actionable callback fails before the bridge records or answers it,
        // XMage remains stuck in waitForResponse() because no response was delivered.
        // Wake MCP waiters so the bridge fails fast instead of hanging until timeout.
        if (actionable) {
            logger.error("[" + client.getUsername() + "] CRITICAL: Actionable callback " + method
                    + " dropped due to exception — declaring player dead to prevent hang");
            playerDead = true;
            synchronized (actionLock) {
                actionLock.notifyAll();
            }
            try {
                processor.submit(BridgeCommand.of(() -> {
                    advancePendingChooseActionFlow();
                    advancePendingPassPriorityFlow();
                    return null;
                }));
            } catch (IllegalStateException ignored) {
                // Processor is already gone; there is nothing left to wake.
            }
        }
    }

    private void storePendingAction(UUID gameId, ClientCallbackMethod method, Object data) {
        String message = extractMessage(data);
        // Capture GameView and game_seq from the decision callback itself,
        // not from lastGameView (which can be updated by later gameUpdate
        // callbacks racing on the callback thread).
        int gameSeq = 0;
        GameView gv = extractGameView(data);
        if (gv != null) {
            updateLastGameView(gv, "storePendingAction:" + method.name());
            gameSeq = gv.getGameSeq();
        }
        PendingAction replacedAction = null;
        PendingAction newAction = new PendingAction(gameId, method, data, message, gameSeq);
        synchronized (actionLock) {
            replacedAction = pendingAction;
            pendingAction = newAction;
            actionLock.notifyAll();
        }
        if (replacedAction != null) {
            String summary = "old=" + summarizePendingAction(replacedAction)
                + ",new=" + summarizePendingAction(newAction);
            logger.warn("[" + client.getUsername() + "] Pending action replaced: " + summary);
            logBridgeEvent("PENDING_ACTION_REPLACED", gameId, summary);
        }
        logger.debug("[" + client.getUsername() + "] Stored pending action: " + method + " - " + message);
        advancePendingChooseActionFlow();
        advancePendingPassPriorityFlow();
    }

    private static GameView extractGameView(Object data) {
        if (data instanceof GameClientMessage gcm) {
            return gcm.getGameView();
        }
        if (data instanceof AbilityPickerView apv) {
            return apv.getGameView();
        }
        return null;
    }

    private String extractMessage(Object data) {
        if (data instanceof GameClientMessage msg) {
            if (msg.getMessage() != null) {
                return msg.getMessage();
            }
            if (msg.getChoice() != null && msg.getChoice().getMessage() != null) {
                return msg.getChoice().getMessage();
            }
        } else if (data instanceof AbilityPickerView picker) {
            return picker.getMessage();
        }
        return "";
    }

    /**
     * Ignore late callbacks from stale games in keepAlive mode.
     *
     * Without this guard, callbacks from an older game can overwrite pendingAction
     * for the current game and strand pass_priority/choose_action waiting on the
     * wrong game flow.
     */
    private String nonCurrentGameCallbackIgnoreReason(UUID callbackGameId, ClientCallbackMethod method) {
        if (callbackGameId == null) {
            return null;
        }

        // START_GAME is intentionally excluded: it's the callback that
        // *establishes* currentGameId, so filtering it would be circular.
        boolean gameScoped = ACTIONABLE_CALLBACKS.contains(method)
                || method == ClientCallbackMethod.GAME_INIT
                || method == ClientCallbackMethod.GAME_OVER
                || method == ClientCallbackMethod.GAME_UPDATE
                || method == ClientCallbackMethod.GAME_UPDATE_AND_INFORM;
        if (!gameScoped) {
            return null;
        }

        UUID gameId = currentGameId;
        if (gameId == null) {
            return "no_current_game_id";
        }
        if (!gameId.equals(callbackGameId)) {
            return "non_current_game";
        }
        if (!activeGames.containsKey(callbackGameId)) {
            return "inactive_game";
        }
        return null;
    }

    private boolean shouldIgnoreNonCurrentGameCallback(
            UUID callbackGameId,
            ClientCallbackMethod method,
            String ignoreReason) {
        if (ignoreReason == null) {
            return false;
        }

        String warnMessage;
        if ("no_current_game_id".equals(ignoreReason)) {
            warnMessage = "Ignoring " + method + " for game " + callbackGameId + " (no currentGameId)";
        } else if ("non_current_game".equals(ignoreReason)) {
            warnMessage = "Ignoring " + method + " for non-current game " + callbackGameId
                + " (currentGameId=" + currentGameId + ")";
        } else if ("inactive_game".equals(ignoreReason)) {
            warnMessage = "Ignoring " + method + " for inactive game " + callbackGameId
                + " (not in activeGames)";
        } else {
            warnMessage = "Ignoring " + method + " for game " + callbackGameId
                + " (reason=" + ignoreReason + ")";
        }
        logger.warn("[" + client.getUsername() + "] " + warnMessage);
        logBridgeEvent(
            "CALLBACK_IGNORED",
            callbackGameId,
            method.name() + " | " + summarizeCallbackContext(callbackGameId, ignoreReason));
        return true;
    }

    /**
     * Clean a string for LLM consumption: strip HTML tags and 3-char hex ID suffixes.
     * Must be applied after internal HTML parsing (cast owner tracking, mana payment extraction).
     */
    private static String stripHtml(String s) {
        return BridgePromptFormatting.stripHtml(s);
    }

    static String stripAbilityPickerOrdinalPrefix(String description, int zeroBasedIndex) {
        return BridgePromptFormatting.stripAbilityPickerOrdinalPrefix(description, zeroBasedIndex);
    }

    // Passive callback: CHATMESSAGE
    // Remaining effects after passive-state audit (see issue: minimize-bridge-passive-callback-state):
    //  REQUIRED  – playerDead detection: early bail-out prevents bridge hangs after elimination
    //  REQUIRED  – unseenChat buffering: surfaces player-to-player chat + system messages via attachUnseenChat()
    //  REQUIRED  – chatLog capture: TALK messages interleaved with bridge events by renderGameLogFlat()
    //  DONE      – gameLog accumulation: migrated to server-side bridge events (epoch 55)
    private void handleChatMessage(Object data) {
        if (data instanceof ChatMessage chatMsg) {
            if (chatMsg.getMessageType() == ChatMessage.MessageType.GAME) {
                String msg = chatMsg.getMessage();
                // Detect when our player has lost the game
                if (!playerDead && msg != null && msg.contains("has lost the game")
                        && msg.contains(client.getUsername())) {
                    playerDead = true;
                    logger.info("[" + client.getUsername() + "] Player death detected from game log");
                }
            } else if (chatMsg.getMessageType() == ChatMessage.MessageType.TALK) {
                String user = chatMsg.getUsername();
                String msg = chatMsg.getMessage();
                if (user != null && msg != null && !msg.isEmpty()) {
                    // Capture chat for game log rendering (interleaved with bridge events).
                    // bridgeEventCursor is the best-known event position; it advances when
                    // pullBridgeEvents() runs. Chat arriving before the first pull gets
                    // cursor=0, placing it before game events — chronologically correct since
                    // the chat predates the first event pull.
                    synchronized (chatLog) {
                        chatLog.add(new BridgeChatLogEntry(bridgeEventCursor, msg, "[Chat] " + user + ": " + msg));
                    }
                    // Buffer chat from other players so pass_priority can surface it
                    if (!user.equals(client.getUsername())) {
                        synchronized (unseenChat) {
                            unseenChat.add(user + ": " + msg);
                        }
                    }
                }
            }
            logger.debug("[" + client.getUsername() + "] Chat: " + chatMsg.getMessage());
        } else {
            logEvent(ClientCallbackMethod.CHATMESSAGE, data);
        }
    }

    private void handleStartGame(UUID gameId, Object data) {
        TableClientMessage message = (TableClientMessage) data;
        UUID startTableId = message.getCurrentTableId();
        if (keepAliveAfterGame && !startGameArmed) {
            logger.warn("[" + client.getUsername() + "] Ignoring START_GAME for table "
                    + startTableId + " because join_table has not armed a next game"
                    + " (gameId=" + gameId + ")");
            return;
        }
        UUID expectedTableId = expectedStartTableId;
        if (expectedTableId != null && !expectedTableId.equals(startTableId)) {
            logger.warn("[" + client.getUsername() + "] Ignoring START_GAME for table "
                    + startTableId + " while waiting for table " + expectedTableId
                    + " (gameId=" + gameId + ")");
            return;
        }
        expectedStartTableId = null;
        startGameArmed = false;
        UUID playerId = message.getPlayerId();
        activeGames.put(gameId, playerId);
        currentGameId = gameId;
        currentPlayerId = playerId;
        gameEverStarted = true;
        shortIds.clear();

        // Join the game session (creates GameSessionPlayer on server)
        if (!session.joinGame(gameId)) {
            logger.error("[" + client.getUsername() + "] Failed to join game: " + gameId);
        }

        // Get chat ID for this game and join to receive incoming messages
        session.getGameChatId(gameId).ifPresent(chatId -> {
            gameChatIds.put(gameId, chatId);
            session.joinChat(chatId);
            logger.info("[" + client.getUsername() + "] Joined game chat: " + chatId);
        });

        logger.info("[" + client.getUsername() + "] Game started: gameId=" + gameId + ", playerId=" + playerId);
        gameStartLatch.countDown();
    }

    private void handleGameInit(Object data) {
        GameView gameView = (GameView) data;
        updateLastGameView(gameView, "GAME_INIT");
        logger.info("[" + client.getUsername() + "] Game initialized: " + gameView.getPlayers().size() + " players");
    }

    // Passive callback: GAME_UPDATE / GAME_UPDATE_AND_INFORM
    // No state mutation — actionable callbacks provide fresh GameViews at decision time via
    // storePendingAction(). Short ID registration for non-CardView objects (players, lookedAt
    // cards) happens in getStableShortId() which checks the GameView's lookedAt zone directly.
    private void logGameState(Object data) {
        if (data instanceof GameView gameView) {
            logger.debug("[" + client.getUsername() + "] Game update: turn " + gameView.getTurn() +
                    ", phase " + gameView.getPhase() + ", active player " + gameView.getActivePlayerName());
        } else if (data instanceof GameClientMessage message) {
            logger.debug("[" + client.getUsername() + "] Game inform: " + message.getMessage());
        }
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

    private String prettyManaType(ManaType manaType) {
        return switch (manaType) {
            case WHITE -> "White";
            case BLUE -> "Blue";
            case BLACK -> "Black";
            case RED -> "Red";
            case GREEN -> "Green";
            case COLORLESS -> "Colorless";
            case GENERIC -> "Generic";
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
    private CopyOnWriteArrayList<ManaPlanEntry> parseManaPlan(String[] arr) {
        var plan = new CopyOnWriteArrayList<ManaPlanEntry>();
        for (String entry : arr) {
            if (isPoolColor(entry)) {
                plan.add(new ManaPlanEntry("pool", entry));
            } else {
                int colonIdx = entry.indexOf(':');
                if (colonIdx >= 0) {
                    String shortId = entry.substring(0, colonIdx);
                    int abilityIndex = Integer.parseInt(entry.substring(colonIdx + 1));
                    plan.add(new ManaPlanEntry("tap", shortId, abilityIndex));
                } else {
                    plan.add(new ManaPlanEntry("tap", entry));
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
        if (payingForId != null) {
            failedManaCasts.add(payingForId);
        }
        manaPlan = null;
        manaPlanAbilityIndex = null;
        synchronized (unseenChat) {
            unseenChat.add("[System] Spell cancelled — mana plan was incorrect or incomplete.");
        }
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
        CopyOnWriteArrayList<ManaPlanEntry> plan = manaPlan;
        if (plan != null && !plan.isEmpty()) {
            ManaPlanEntry entry = plan.remove(0);  // consume first entry

            if ("tap".equals(entry.type())) {
                manaPlanAbilityIndex = entry.abilityIndex();  // save for GAME_CHOOSE_ABILITY
                UUID targetId = shortIds.tryResolve(entry.value());
                if (targetId == null) {
                    logger.warn("[" + client.getUsername() + "] Mana plan: unknown short ID '" + entry.value() + "', cancelling spell");
                    return cancelSpellFromBadManaPlan(gameId, payingForId);
                }
                PlayableObjectsList playableForPlan = gameView != null ? gameView.getCanPlayObjects() : null;
                if (playableForPlan != null) {
                    PlayableObjectStats stats = playableForPlan.getObjects().get(targetId);
                    if (stats != null && !targetId.equals(payingForId) && !failedManaCasts.contains(targetId)) {
                        logger.info("[" + client.getUsername() + "] Mana plan: \"" + msg + "\" -> tapping " + entry.value());
                        poolManaAttempts = 0;
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
            if (manaPlanAutoTapFallback) {
                logger.info("[" + client.getUsername() + "] Mana plan: exhausted, falling through to auto-tap for remaining pips");
                manaPlan = null;
                manaPlanAbilityIndex = null;
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
                if (failedManaCasts.contains(objectId)) {
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
                    poolManaAttempts = 0; // Reset pool counter — tap may produce needed mana
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
                if (payingForId != null && payingForId.equals(poolManaPayingForId)) {
                    poolManaAttempts++;
                } else {
                    poolManaPayingForId = payingForId;
                    poolManaAttempts = 1;
                }
                if (poolManaAttempts > MAX_POOL_MANA_ATTEMPTS) {
                    logger.warn("[" + client.getUsername() + "] Mana: \"" + msg + "\" -> pool payment not progressing after "
                            + poolManaAttempts + " attempts, cancelling spell");
                    poolManaAttempts = 0;
                    poolManaPayingForId = null;
                    manaPlan = null;
                    manaPlanAbilityIndex = null;
                    if (payingForId != null) {
                        failedManaCasts.add(payingForId);
                    }
                    synchronized (unseenChat) {
                        unseenChat.add("[System] Spell cancelled — not enough mana to complete payment.");
                    }
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
        if (payingForId != null) {
            failedManaCasts.add(payingForId);
        }
        manaPlan = null;
        manaPlanAbilityIndex = null;
        synchronized (unseenChat) {
            unseenChat.add("[System] Spell cancelled — not enough mana to complete payment.");
        }
        logBridgeEvent("SPELL_CANCELLED", "not enough mana to complete payment");
        sendBooleanOrDie(gameId, false, "manaAuto:no_source_cancel");
        return true;
    }

    /**
     * Shared cleanup for game-end handlers: remove from active tracking,
     * wake action waiters, and leave the game chat.
     *
     * @return true if the game was still in activeGames (i.e. not yet cleaned up)
     */
    private boolean cleanupGame(UUID gameId) {
        boolean wasActive = activeGames.remove(gameId) != null;
        synchronized (actionLock) {
            actionLock.notifyAll();
        }
        UUID chatId = gameChatIds.remove(gameId);
        if (chatId != null) {
            session.leaveChat(chatId);
        }
        return wasActive;
    }

    private void handleGameOver(UUID gameId, Object data) {
        GameClientMessage message = (GameClientMessage) data;

        // Update lastGameView with the final game-over GameView BEFORE
        // removing from activeGames.  The game-over callback carries the
        // authoritative final GameView with the deterministic game_seq.
        // Without this, passPriority's game-over bail-out reads
        // lastGameView from the last asynchronous gameUpdate push, whose
        // game_seq depends on how many updates arrived before this callback
        // — causing nondeterministic game_seq in tool results and golden
        // test flakes.
        GameView gv = message.getGameView();
        if (gv != null) {
            updateLastGameView(gv, "handleGameOver");
        }
        // Do not pull bridge events synchronously from the callback thread.
        // The server caches them during removeGame(), and currentPlayerId lets
        // postgame get_game_history calls fetch them after activeGames clears.
        cleanupGame(gameId);
        logger.info("[" + client.getUsername() + "] Game over: " + message.getMessage());

        if (keepAliveAfterGame) {
            // Multi-game session: signal game finished but keep the client alive.
            // The Python side (join_table tool) drives the next game.
            logger.info("[" + client.getUsername() + "] Game ended (keepAlive mode, staying connected)");
            gameFinishedLatch.countDown();
        } else {
            // Each game gets its own pilot process + bridge client.
            // Disconnect immediately so the XMage server doesn't auto-join us
            // into the next game in a parallel gauntlet.
            logger.info("[" + client.getUsername() + "] Game ended, stopping client");
            client.stop();
        }
        advancePendingChooseActionFlow();
        advancePendingPassPriorityFlow();
    }

    /**
     * Safety net for dropped GAME_OVER callbacks.
     *
     * The server dispatches END_GAME_INFO after GAME_OVER (via
     * GameController.endGame → tableManager.endGame → match.endGame →
     * fireGameEndInfo).  In the normal case handleGameOver() already cleaned
     * up and every operation below is an idempotent no-op.
     *
     * If GAME_OVER was lost (Session lock timeout, callback delivery failure),
     * this ensures the bridge still detects the game ended instead of spinning
     * in passPriority indefinitely.
     */
    private void handleEndGameInfo(UUID gameId) {
        boolean wasActive = cleanupGame(gameId);
        if (!wasActive) {
            logger.info("[" + client.getUsername() + "] End game info received for game " + gameId);
            return;
        }
        // GAME_OVER was missed — perform the shutdown that handleGameOver would have done.
        logger.warn("[" + client.getUsername() + "] END_GAME_INFO cleaning up game " + gameId
            + " (GAME_OVER was likely dropped)");
        if (keepAliveAfterGame) {
            gameFinishedLatch.countDown();
        } else {
            logger.info("[" + client.getUsername() + "] END_GAME_INFO stopping client (missed GAME_OVER)");
            client.stop();
        }
        advancePendingChooseActionFlow();
        advancePendingPassPriorityFlow();
    }

    private void handleUserRequestDialog(Object data) {
        UserRequestMessage request = (UserRequestMessage) data;
        // Auto-accept hand permission requests from observers
        if (request.getButton1Action() == PlayerAction.ADD_PERMISSION_TO_SEE_HAND_CARDS) {
            UUID gameId = request.getGameId();
            UUID relatedUserId = request.getRelatedUserId();
            logger.info("[" + client.getUsername() + "] Auto-granting hand permission to " + request.getRelatedUserName());
            session.sendPlayerAction(PlayerAction.ADD_PERMISSION_TO_SEE_HAND_CARDS, gameId, relatedUserId);
        } else {
            logger.debug("[" + client.getUsername() + "] Ignoring user request dialog: " + request.getTitle());
        }
    }

    private void logEvent(ClientCallbackMethod method, Object data) {
        logger.debug("[" + client.getUsername() + "] Event: " + method + " - " + data);
    }
}
