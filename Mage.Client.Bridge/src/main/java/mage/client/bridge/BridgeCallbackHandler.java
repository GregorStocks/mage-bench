package mage.client.bridge;

import mage.cards.decks.DeckCardInfo;
import mage.cards.decks.DeckCardLists;
import mage.game.BridgeLogEntry;
import mage.cards.repository.CardInfo;
import mage.cards.repository.CardRepository;
import mage.choices.Choice;
import mage.constants.CardType;
import mage.constants.ManaType;
import mage.constants.PhaseStep;
import mage.constants.PlayerAction;
import mage.constants.SubType;
import mage.constants.SubTypeSet;
import mage.constants.SuperType;
import mage.interfaces.callback.ClientCallback;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.remote.Session;
import mage.view.AbilityPickerView;
import mage.view.CommandObjectView;
import mage.view.CounterView;
import mage.view.CardsView;
import mage.view.CardView;
import mage.view.ChatMessage;
import mage.view.CombatGroupView;
import mage.view.ExileView;
import mage.view.GameClientMessage;
import mage.view.GameView;
import mage.view.LookedAtView;
import mage.view.ManaPoolView;
import mage.view.PermanentView;
import mage.view.PlayerView;
import mage.view.SimpleCardView;
import mage.view.TableClientMessage;
import mage.view.UserRequestMessage;
import mage.players.PlayableObjectsList;
import mage.players.PlayableObjectStats;
import mage.util.MultiAmountMessage;
import mage.util.ShortIdRegistry;

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
import java.util.TreeMap;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Callback handler for the bridge client.
 * Supports multiple modes:
 * - potato mode (default): Always passes priority and chooses the first available option
 * - staller mode: Same decisions as potato, but intentionally delayed and kept alive between games
 * - MCP mode (sleepwalker): Stores pending actions for external client to handle via MCP
 */
public class BridgeCallbackHandler {

    private static final Logger logger = Logger.getLogger(BridgeCallbackHandler.class);
    private static final int DEFAULT_ACTION_DELAY_MS = 500;
    private static final int MAX_GAME_LOG_CHARS = 5 * 1024 * 1024; // 5MB cap on in-memory game log buffer

    // Regex patterns to detect colored mana symbols inside braces, including hybrid/phyrexian variants.
    // Same approach as ManaUtil.java — \x7b = {, \x7d = }, .{0,2} allows up to 2 chars on each side.
    // Matches {W}, {W/U}, {W/P}, {W/U/P}, {2/W}, {C/W}, etc.
    private static final Pattern REGEX_WHITE = Pattern.compile("\\x7b.{0,2}W.{0,2}\\x7d");
    private static final Pattern REGEX_BLUE = Pattern.compile("\\x7b.{0,2}U.{0,2}\\x7d");
    private static final Pattern REGEX_BLACK = Pattern.compile("\\x7b.{0,2}B.{0,2}\\x7d");
    private static final Pattern REGEX_RED = Pattern.compile("\\x7b.{0,2}R.{0,2}\\x7d");
    private static final Pattern REGEX_GREEN = Pattern.compile("\\x7b.{0,2}G.{0,2}\\x7d");
    private static final Pattern REGEX_COLORLESS = Pattern.compile("\\x7b.{0,2}C.{0,2}\\x7d");
    // Pattern to match "TURN <number>" at the start of game log messages
    private static final Pattern TURN_MSG_PATTERN = Pattern.compile("^TURN \\d+");
    // Pattern to extract player name and object_id from cast messages in game chat HTML
    private static final Pattern CAST_OWNER_PATTERN = Pattern.compile(
            "<font[^>]*>([^<]+)</font>\\s+casts\\s+.*?object_id='([^']+)'");
    // Pattern to strip HTML tags from XMage messages before sending to LLMs
    private static final Pattern HTML_TAG_PATTERN = Pattern.compile("<[^>]+>");
    // Pattern to strip 3-char hex ID suffixes (e.g. " [8ad]") that XMage appends to card names.
    // These are the first 3 chars of the object UUID, useful for the Swing UI but confusing for LLMs.
    private static final Pattern HEX_SUFFIX_PATTERN = Pattern.compile(" \\[[0-9a-f]{3}\\]");

    private final BridgeMageClient client;
    private Session session;
    private final Map<UUID, UUID> activeGames = new ConcurrentHashMap<>(); // gameId -> playerId
    private final Map<UUID, UUID> gameChatIds = new ConcurrentHashMap<>(); // gameId -> chatId

    // MCP mode fields
    private volatile boolean mcpMode = false;
    private volatile int actionDelayMs = DEFAULT_ACTION_DELAY_MS;
    private volatile int actionsProcessed = 0;
    private static final int STALLER_WARMUP_ACTIONS = 20;
    private volatile boolean keepAliveAfterGame = false;
    private volatile boolean gameEverStarted = false;
    private volatile PendingAction pendingAction = null;
    private final Object actionLock = new Object(); // For wait_for_action blocking
    private final StringBuilder gameLog = new StringBuilder();
    private int gameLogTrimmedChars = 0; // tracks chars trimmed from front so offset-based access stays valid
    private volatile UUID currentGameId = null;
    private volatile GameView lastGameView = null;
    private final RoundTracker roundTracker = new RoundTracker();

    /** Update lastGameView and feed the RoundTracker. */
    private synchronized void updateLastGameView(GameView gv) {
        updateLastGameView(gv, null);
    }

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
            registerNonCardViewShortIds(gv);
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

    /**
     * Pre-register server-assigned short IDs for objects not found by findCardViewById.
     * Called when a new GameView is received so that player UUIDs and lookedAt cards
     * get p-prefix IDs instead of l-prefix fallbacks.
     */
    private void registerNonCardViewShortIds(GameView gv) {
        // Players (PlayerView is not a CardView, so findCardViewById never finds them)
        for (PlayerView pv : gv.getPlayers()) {
            String serverShortId = pv.getShortId();
            if (serverShortId != null && !serverShortId.isBlank()) {
                shortIds.register(pv.getPlayerId(), serverShortId);
            }
        }
        // LookedAt cards (SimpleCardView, not searchable by findCardViewById)
        for (LookedAtView lv : gv.getLookedAt()) {
            for (SimpleCardView sv : lv.getCards().values()) {
                String serverShortId = sv.getShortId();
                if (serverShortId != null && !serverShortId.isBlank()) {
                    shortIds.register(sv.getId(), serverShortId);
                }
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
    private volatile String lastManaPaymentPrompt = null; // Last GAME_PLAY_MANA prompt text for ability color matching
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
    private final Map<String, String> castOwners = new HashMap<>(); // objectId → playerName from cast messages
    private final Map<String, Integer> playerTurnCounts = new HashMap<>(); // playerName → per-player turn count
    private volatile String lastChatMessage = null; // For deduplicating outgoing chat
    private volatile long lastChatTimeMs = 0; // Timestamp of last outgoing chat
    private static final long CHAT_DEDUP_WINDOW_MS = 30_000; // Suppress identical messages within 30s
    private volatile int bridgeEventCursor = 0; // Pull cursor for bridge event log
    private final List<BridgeLogEntry> cachedBridgeEvents = new ArrayList<>(); // Client-side cache survives game cleanup

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
    private record TargetChoice(UUID targetId, Map<String, Object> entry) {
    }
    private volatile long lastCallbackReceivedAt = 0;
    private volatile UUID lastCallbackGameId = null;
    // Track actionable callbacks (GAME_SELECT, GAME_ASK, etc.) separately from passive
    // ones (CHATMESSAGE, GAME_UPDATE). Used by zombie detection and progress logging.
    private static final EnumSet<ClientCallbackMethod> ACTIONABLE_CALLBACKS = EnumSet.of(
        ClientCallbackMethod.GAME_SELECT, ClientCallbackMethod.GAME_ASK,
        ClientCallbackMethod.GAME_TARGET, ClientCallbackMethod.GAME_CHOOSE_ABILITY,
        ClientCallbackMethod.GAME_CHOOSE_CHOICE, ClientCallbackMethod.GAME_CHOOSE_PILE,
        ClientCallbackMethod.GAME_PLAY_MANA, ClientCallbackMethod.GAME_PLAY_XMANA,
        ClientCallbackMethod.GAME_GET_AMOUNT, ClientCallbackMethod.GAME_GET_MULTI_AMOUNT);
    private volatile long lastActionableCallbackAt = 0;
    private static final long POST_ACTION_WAIT_MS = 10_000; // how long to optimistically wait for next callback after an action
    private static final long ZOMBIE_GAME_TIMEOUT_MS = 60 * 60 * 1000; // no actionable callback for 60min = zombie
    private static final ZoneId LOG_TZ = ZoneId.of("America/Los_Angeles");
    private static final DateTimeFormatter TIME_FMT = DateTimeFormatter.ISO_OFFSET_DATE_TIME;

    public BridgeCallbackHandler(BridgeMageClient client) {
        this.client = client;
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
    private void logBridgeEvent(ClientCallbackMethod method, String summary) {
        logBridgeEvent(method.name(), summary);
    }

    private void logBridgeEvent(String method, String summary) {
        String path = bridgeLogPath;
        if (path == null) {
            return;
        }
        try (PrintWriter pw = new PrintWriter(new FileWriter(path, true))) {
            var sb = new StringBuilder();
            sb.append("{\"ts\":\"").append(ZonedDateTime.now(LOG_TZ).format(TIME_FMT)).append("\"");
            sb.append(",\"method\":\"").append(method).append("\"");
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
        UUID myPlayerId = gameId != null ? activeGames.get(gameId) : null;
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

    public void setSession(Session session) {
        this.session = session;
    }

    public void setMcpMode(boolean enabled) {
        this.mcpMode = enabled;
        logger.info("[" + client.getUsername() + "] MCP mode " + (enabled ? "enabled" : "disabled"));
    }

    public boolean isMcpMode() {
        return mcpMode;
    }

    public void setActionDelayMs(int actionDelayMs) {
        this.actionDelayMs = Math.max(0, actionDelayMs);
        logger.info("[" + client.getUsername() + "] action delay set to " + this.actionDelayMs + " ms");
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
        BridgeCallbackHandler fresh = new BridgeCallbackHandler(client);
        fresh.session = this.session;
        fresh.mcpMode = this.mcpMode;
        fresh.actionDelayMs = this.actionDelayMs;
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
     * Block until {@code handleGameOver()} fires. Used by potato keepAlive loop.
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
        mage.cards.decks.DeckCardLists deck = BridgeClient.loadDeck(deckPath);
        fresh.setDeckList(deck);
        UUID tableId = jh.joinTable(deckPath, targetTableId);
        assert tableId != null : "Failed to join any table within timeout";
        logger.info("[" + client.getUsername() + "] Joined table " + tableId + ", waiting for game start...");
        boolean started = fresh.awaitGameStart(60_000);
        assert started : "Game did not start within 60s after joining table";
        logger.info("[" + client.getUsername() + "] Game started after join_table");
    }

    public void reset() {
        activeGames.clear();
        gameChatIds.clear();
        pendingAction = null;
        currentGameId = null;
        gameEverStarted = false;
        lastGameView = null;
        lastChoices = null;
        actionsProcessed = 0;
        lastActionableCallbackAt = 0;
        cachedBridgeEvents.clear();
        bridgeEventCursor = 0;
        synchronized (gameLog) {
            gameLog.setLength(0);
            gameLogTrimmedChars = 0;
        }
    }

    private void sleepBeforeAction() {
        int delay = actionDelayMs;
        if (actionsProcessed < STALLER_WARMUP_ACTIONS) {
            delay = Math.min(delay, DEFAULT_ACTION_DELAY_MS);
            actionsProcessed++;
        }
        try {
            Thread.sleep(delay);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    // MCP mode methods

    public boolean isActionPending() {
        return pendingAction != null;
    }

    public Map<String, Object> executeDefaultAction() {
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
                session.sendPlayerBoolean(gameId, false);
                result.put("action_taken", "passed_priority");
            }
            case GAME_PLAY_MANA, GAME_PLAY_XMANA -> {
                // Auto-tap failed; default action is to cancel the spell
                session.sendPlayerBoolean(gameId, false);
                result.put("action_taken", "cancelled_mana");
            }
            case GAME_TARGET -> {
                GameClientMessage targetMsg = (GameClientMessage) data;
                boolean required = targetMsg.isFlag();
                // Try to find valid targets from multiple sources
                Set<UUID> targets = findValidTargets(targetMsg);
                if (required && targets != null && !targets.isEmpty()) {
                    UUID firstTarget = selectDeterministicTarget(targets, null);
                    session.sendPlayerUUID(gameId, firstTarget);
                    result.put("action_taken", "selected_first_target");
                } else {
                    session.sendPlayerBoolean(gameId, false);
                    result.put("action_taken", "cancelled");
                }
            }
            case GAME_CHOOSE_ABILITY -> {
                AbilityPickerView picker = (AbilityPickerView) data;
                Map<UUID, String> abilityChoices = picker.getChoices();
                if (abilityChoices != null && !abilityChoices.isEmpty()) {
                    UUID firstChoice = abilityChoices.keySet().iterator().next();
                    session.sendPlayerUUID(gameId, firstChoice);
                    result.put("action_taken", "selected_first_ability");
                } else {
                    session.sendPlayerUUID(gameId, null);
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
                            session.sendPlayerString(gameId, firstKey);
                            result.put("action_taken", "selected_first_key_choice");
                        } else {
                            session.sendPlayerString(gameId, null);
                            result.put("action_taken", "no_choices");
                        }
                    } else {
                        Set<String> choices = choice.getChoices();
                        if (choices != null && !choices.isEmpty()) {
                            String firstChoice = choices.iterator().next();
                            session.sendPlayerString(gameId, firstChoice);
                            result.put("action_taken", "selected_first_choice");
                        } else {
                            session.sendPlayerString(gameId, null);
                            result.put("action_taken", "no_choices");
                        }
                    }
                } else {
                    session.sendPlayerString(gameId, null);
                    result.put("action_taken", "null_choice");
                }
            }
            case GAME_CHOOSE_PILE -> {
                session.sendPlayerBoolean(gameId, true);
                result.put("action_taken", "selected_pile_1");
            }
            case GAME_GET_AMOUNT -> {
                GameClientMessage amountMsg = (GameClientMessage) data;
                int min = amountMsg.getMin();
                session.sendPlayerInteger(gameId, min);
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
                session.sendPlayerString(gameId, sb.toString());
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
    public Map<String, Object> getActionChoices(Long boardCursorParam) {
        var result = new HashMap<String, Object>();
        PendingAction action = pendingAction;
        // Prefer the action's own GameView over lastGameView — a concurrent GAME_UPDATE
        // can overwrite lastGameView with a view from a different phase (race condition).
        GameView gameView = null;
        if (action != null && action.data() instanceof GameClientMessage) {
            gameView = ((GameClientMessage) action.data()).getGameView();
        }
        if (gameView == null) {
            gameView = lastGameView;
        }
        // Capture for use in lambdas (must be effectively final).
        final GameView gv = gameView;
        if (action != null) {
            result.put("game_seq", action.gameSeq());
        }

        if (action == null) {
            result.put("action_pending", false);
            clearChoiceSnapshot();
            attachUnseenChat(result);
            return result;
        }

        result.put("action_pending", true);
        result.put("action_type", action.method().name());
        result.put("message", stripHtml(action.message()));

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
            result.put("context", ctx.toString());

            // Full board state: players with battlefield, graveyard, exile, hand, etc.
            // Board cursor dedup: skip the board payload when caller already has it.
            List<Map<String, Object>> players = buildPlayersArray(gameView);
            long currentBoardCursor = updateBoardCursor(players);
            result.put("board_cursor", currentBoardCursor);
            if (boardCursorParam != null && boardCursorParam.longValue() == currentBoardCursor) {
                result.put("board_unchanged", true);
            } else {
                result.put("board", players);
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
                    result.put("untapped_lands", untappedLands);
                }
            }
            // Analogous to Arena highlighting your lands when you have a land drop left.
            // Helps LLMs remember they can play a land this turn.
            // Uses the authoritative server value from PlayerView, not chat-message counting.
            if (isMyTurn && isMainPhase && myPlayer != null) {
                result.put("land_drops_used", myPlayer.getLandsPlayed());
            }

            // Stack summary — helps LLMs see what's pending before casting instants/counters
            if (gameView.getStack() != null && !gameView.getStack().isEmpty()) {
                var stackSummary = new ArrayList<Map<String, Object>>();
                for (CardView card : gameView.getStack().values()) {
                    var item = new HashMap<String, Object>();
                    item.put("name", safeDisplayName(card));
                    if (card.getId() != null) {
                        String owner = castOwners.get(card.getId().toString());
                        if (owner != null) {
                            item.put("owner", owner);
                        }
                    }
                    if (card.getTargets() != null && !card.getTargets().isEmpty()) {
                        var targets = new ArrayList<Map<String, Object>>();
                        for (UUID targetId : card.getTargets()) {
                            var t = new HashMap<String, Object>();
                            t.put("id", getStableShortId(targetId, findCardViewById(targetId, gameView)));
                            t.put("name", describeTarget(targetId, null, gameView));
                            targets.add(t);
                        }
                        item.put("targets", targets);
                    }
                    stackSummary.add(item);
                }
                result.put("stack", stackSummary);
            }

            // Combat context — show attackers/blockers during any combat step
            // so LLMs see the combat state when casting instants or activating abilities
            List<Map<String, Object>> combatGroups = buildCombatGroups(gameView);
            if (combatGroups != null) {
                result.put("combat", combatGroups);
            }
        }

        ClientCallbackMethod method = action.method();
        Object data = action.data();

        switch (method) {
            case GAME_ASK: {
                result.put("response_type", "boolean");
                result.put("respond_with", "choice=yes or choice=no");
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
                        result.put("your_hand", handCards);
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
                if (data instanceof GameClientMessage) {
                    GameClientMessage gcm = (GameClientMessage) data;
                    Map<String, Serializable> options = gcm.getOptions();
                    if (options != null) {
                        @SuppressWarnings("unchecked")
                        List<UUID> possibleAttackerIds = (List<UUID>) options.get("possibleAttackers");
                        @SuppressWarnings("unchecked")
                        List<UUID> possibleBlockerIds = (List<UUID>) options.get("possibleBlockers");

                        if (possibleAttackerIds != null && !possibleAttackerIds.isEmpty()) {
                            result.put("combat_phase", "declare_attackers");

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
                                result.put("already_attacking", alreadyAttacking);
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
                            result.put("combat_phase", "declare_blockers");

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
                                result.put("incoming_attackers", incomingAttackers);
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
                    result.put("response_type", "select");
                    result.put("choices", choiceList);
                    lastChoices = indexToUuid;
                    String combatPhase = (String) result.get("combat_phase");
                    if ("declare_attackers".equals(combatPhase)) {
                        result.put("respond_with", "attackers=p1,p2,... or choice=yes (confirm) or choice=no (skip)");
                    } else if ("declare_blockers".equals(combatPhase)) {
                        result.put("respond_with", "blockers=p5:p1,p6:p2 (blocker:attacker) or choice=yes (confirm) or choice=no (skip)");
                    } else {
                        result.put("respond_with", "choice=pN to play, or choice=no to pass");
                    }
                } else {
                    result.put("response_type", "boolean");
                    result.put("respond_with", "choice=yes (confirm) or choice=no (pass)");
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
                    result.put("response_type", "select");
                    result.put("respond_with", "choice=pN to tap, or choice=no to cancel");
                    result.put("choices", manaChoiceList);
                    lastChoices = manaIndexToChoice;
                } else {
                    result.put("response_type", "boolean");
                    result.put("respond_with", "choice=no to cancel");
                    lastChoices = null;
                }
                break;
            }

            case GAME_TARGET: {
                GameClientMessage msg = (GameClientMessage) data;
                result.put("response_type", "index");
                boolean required = msg.isFlag();
                result.put("required", required);
                result.put("can_cancel", !required);
                result.put("respond_with", required
                    ? "choice=pN — must pick a target"
                    : "choice=pN, or choice=no to cancel");

                Set<UUID> targets = findValidTargets(msg);
                var choiceList = new ArrayList<Map<String, Object>>();
                var indexToUuid = new ArrayList<Object>();

                if (targets != null) {
                    CardsView cardsView = msg.getCardsView1();
                    GameView targetGameView = msg.getGameView() != null ? msg.getGameView() : lastGameView;
                    UUID gameId = currentGameId;
                    UUID myPlayerId = gameId != null ? activeGames.get(gameId) : null;
                    var targetChoices = new ArrayList<TargetChoice>();
                    for (UUID targetId : targets) {
                        var choiceEntry = new HashMap<String, Object>();
                        // ID assigned after sorting — see below
                        buildTargetInfo(choiceEntry, targetId, cardsView, targetGameView, myPlayerId);
                        targetChoices.add(new TargetChoice(targetId, choiceEntry));
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
                            getStableShortIdSequence(a.targetId(), findCardViewById(a.targetId(), gv)),
                            getStableShortIdSequence(b.targetId(), findCardViewById(b.targetId(), gv)));
                    });

                    int idx = 0;
                    for (TargetChoice tc : targetChoices) {
                        tc.entry().put("id", getStableShortId(tc.targetId(), findCardViewById(tc.targetId(), gameView)));
                        tc.entry().put("index", idx);
                        choiceList.add(tc.entry());
                        indexToUuid.add(tc.targetId());
                        idx++;
                    }
                }

                // Optional GAME_TARGET with no valid targets: auto-cancel
                if (choiceList.isEmpty() && !required) {
                    synchronized (actionLock) {
                        if (pendingAction == action) {
                            pendingAction = null;
                        }
                    }
                    session.sendPlayerBoolean(currentGameId, false);
                    result.put("action_pending", false);
                    result.put("action_taken", "auto_cancelled_no_targets");
                    result.put("message", stripHtml(msg.getMessage()));
                    lastChoices = null;
                    break;
                }

                result.put("choices", choiceList);
                lastChoices = indexToUuid;
                break;
            }

            case GAME_CHOOSE_ABILITY: {
                AbilityPickerView picker = (AbilityPickerView) data;
                Map<UUID, String> choices = picker.getChoices();
                result.put("response_type", "index");
                result.put("respond_with", "choice=0, choice=1, etc. (not yes/no)");

                var choiceList = new ArrayList<Map<String, Object>>();
                var indexToUuid = new ArrayList<Object>();

                boolean allManaAbilities = choices != null && !choices.isEmpty();
                if (choices != null) {
                    int idx = 0;
                    for (Map.Entry<UUID, String> entry : choices.entrySet()) {
                        var choiceEntry = new HashMap<String, Object>();
                        choiceEntry.put("index", idx);
                        String desc = stripHtml(entry.getValue());
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
                    String msg = (String) result.get("message");
                    if (msg != null && msg.startsWith("Choose spell or ability")) {
                        // Extract the card name after ": " (from stripHtml's <br> replacement)
                        int colonIdx = msg.indexOf(": ");
                        String cardName = colonIdx >= 0 ? msg.substring(colonIdx + 2).trim() : "";
                        if (!cardName.isEmpty()) {
                            result.put("message", "Choose which mana to produce from " + cardName
                                    + " (tapping to pay for a spell)");
                        }
                    }
                }

                result.put("choices", choiceList);
                lastChoices = indexToUuid;
                break;
            }

            case GAME_CHOOSE_CHOICE: {
                GameClientMessage msg = (GameClientMessage) data;
                Choice choice = msg.getChoice();
                result.put("response_type", "index");
                result.put("respond_with", "choice=0, choice=1, etc. or text=Name (not yes/no)");

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
                            result.put("note", "Showing " + filtered.size()
                                + " types from your deck (" + totalChoices
                                + " total available). Use choose_action(text='TypeName') for any other type.");
                        }
                    }
                }

                result.put("choices", choiceList);
                lastChoices = indexToKey;
                break;
            }

            case GAME_CHOOSE_PILE: {
                GameClientMessage msg = (GameClientMessage) data;
                result.put("response_type", "pile");
                result.put("respond_with", "pile=1 or pile=2");

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
                result.put("pile1", pile1);
                result.put("pile2", pile2);
                lastChoices = null;
                break;
            }

            case GAME_GET_AMOUNT: {
                GameClientMessage msg = (GameClientMessage) data;
                result.put("response_type", "amount");
                result.put("respond_with", "amount=N (min=" + msg.getMin() + ", max=" + msg.getMax() + ")");
                result.put("min", msg.getMin());
                result.put("max", msg.getMax());
                lastChoices = null;
                break;
            }

            case GAME_GET_MULTI_AMOUNT: {
                GameClientMessage msg = (GameClientMessage) data;
                result.put("response_type", "multi_amount");
                result.put("respond_with", "amounts=[N,N,...] — one per item, sum between total_min and total_max");
                result.put("total_min", msg.getMin());
                result.put("total_max", msg.getMax());

                var items = new ArrayList<Map<String, Object>>();
                if (msg.getMessages() != null) {
                    for (MultiAmountMessage mam : msg.getMessages()) {
                        var item = new HashMap<String, Object>();
                        item.put("description", mam.message);
                        item.put("min", mam.min);
                        item.put("max", mam.max);
                        item.put("default", mam.defaultValue);
                        items.add(item);
                    }
                }
                result.put("items", items);
                lastChoices = null;
                break;
            }

            default:
                result.put("response_type", "unknown");
                result.put("error", "Unhandled action type: " + method);
                lastChoices = null;
        }

        String responseType = (String) result.get("response_type");
        if (responseType != null) {
            int choiceCount = -1;
            if (result.get("choices") instanceof List<?>) {
                choiceCount = ((List<?>) result.get("choices")).size();
            }
            recordChoiceSnapshot(method.name(), responseType, choiceCount);
        } else {
            clearChoiceSnapshot();
        }

        return result;
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
    private void attachChoicesToError(Map<String, Object> errorResult) {
        Map<String, Object> choicesResult = getActionChoices(null);
        if (choicesResult.containsKey("choices")) {
            errorResult.put("choices", choicesResult.get("choices"));
        }
    }

    /**
     * Build a standardized error response for choose_action failures.
     * Must reuse the caller's result map so the finally block can read success=false.
     */
    private Map<String, Object> buildError(Map<String, Object> result, String errorCode,
            String message, boolean retryable, PendingAction action, boolean attachChoices) {
        result.put("success", false);
        result.put("error", message);
        result.put("error_code", errorCode);
        result.put("retryable", retryable);
        pendingAction = action;
        if (attachChoices) {
            attachChoicesToError(result);
        }
        attachUnseenChat(result);
        return result;
    }

    private Map<String, Object> buildError(Map<String, Object> result, String errorCode,
            String message, boolean retryable, PendingAction action) {
        return buildError(result, errorCode, message, retryable, action, false);
    }

    /**
     * Respond to the current pending action with a specific choice.
     * Exactly one parameter should be non-null, matching the response_type from getActionChoices().
     */
    public Map<String, Object> chooseAction(Integer index, String id, Boolean answer, Integer amount, int[] amounts, Integer pile, String text, String[] manaPlanArray, Boolean autoTap, String[] attackers, String[] blockersArray) {
        interactionsThisTurn++;
        var result = new HashMap<String, Object>();
        PendingAction action = pendingAction;
        if (action != null) {
            result.put("game_seq", action.gameSeq());
        }

        // Block-wait for a pending action (like pass_priority does).
        // The LLM may call choose_action before the next callback arrives
        // (e.g. double choose_action in one response, or calling it before
        // pass_priority). Wait instead of failing immediately.
        if (action == null) {
            long waitStart = System.currentTimeMillis();
            synchronized (actionLock) {
                while ((action = pendingAction) == null) {
                    if (playerDead || (activeGames.isEmpty() && gameEverStarted) || !client.isRunning()) {
                        break;
                    }
                    if (System.currentTimeMillis() - waitStart > 10_000) {
                        break;
                    }
                    try {
                        actionLock.wait(200);
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                        break;
                    }
                }
            }
            if (action == null) {
                return buildError(result, "no_pending_action", "No pending action after 10s wait", false, null);
            }
            logger.info("[" + client.getUsername() + "] choose_action: waited "
                + (System.currentTimeMillis() - waitStart) + "ms for pending action");
            result.put("game_seq", action.gameSeq());
        }

        // Loop detection: model has made too many interactions this turn — auto-handle
        if (interactionsThisTurn > maxInteractionsPerTurn) {
            logger.warn("[" + client.getUsername() + "] Loop detected (" + interactionsThisTurn
                + " interactions this turn), auto-handling " + action.method().name());
            // Not a critical error — LLM is stuck in a loop, not a code bug
            executeDefaultAction();
            result.put("success", true);
            result.put("action_taken", "auto_passed_loop_detected");
            result.put("warning", "Too many interactions this turn (" + interactionsThisTurn + "). Auto-passing until next turn.");
            return result;
        }

        // Batch combat: attackers
        if (attackers != null && attackers.length > 0) {
            String combatType = detectCombatSelect(action);
            if ("attackers".equals(combatType)) {
                return handleBatchAttackers(attackers, action, result);
            }
            // Not in declare_attackers — ignore the param and fall through
            logger.warn("[" + client.getUsername() + "] choose_action: ignoring attackers param (not in declare_attackers)");
            result.put("warning", "Ignored attackers parameter (not in declare_attackers phase)");
            attackers = null;
        }

        // Batch combat: blockers
        if (blockersArray != null && blockersArray.length > 0) {
            String combatType = detectCombatSelect(action);
            if ("blockers".equals(combatType)) {
                return handleBatchBlockers(blockersArray, action, result);
            }
            // Not in declare_blockers — ignore the param and fall through
            logger.warn("[" + client.getUsername() + "] choose_action: ignoring blockers param (not in declare_blockers)");
            result.put("warning", "Ignored blockers parameter (not in declare_blockers phase)");
            blockersArray = null;
        }

        // Resolve id to index
        if (id != null) {
            if (index != null) {
                // Both provided — prefer id (it's more specific; index is usually a default value)
                logger.warn("[" + client.getUsername() + "] choose_action: both id=" + id + " and index=" + index + " provided, preferring id");
                result.put("warning", "Both id and index provided; used id=" + id + ", ignored index=" + index);
                index = null;
            }
            List<Object> choices = lastChoices;
            if (choices == null) {
                getActionChoices(null);
                choices = lastChoices;
            }
            if ("all".equals(id)) {
                // Find the "special" entry in lastChoices
                if (choices != null) {
                    for (int i = 0; i < choices.size(); i++) {
                        if ("special".equals(choices.get(i))) {
                            index = i;
                            break;
                        }
                    }
                }
                if (index == null) {
                    return buildError(result, "invalid_choice",
                        "\"all\" is not available in current choices", true, action, true);
                }
            } else {
                UUID resolvedUuid = shortIds.resolve(id);
                if (choices != null) {
                    for (int i = 0; i < choices.size(); i++) {
                        if (resolvedUuid.equals(choices.get(i))) {
                            index = i;
                            break;
                        }
                    }
                }
                if (index == null) {
                    return buildError(result, "invalid_choice",
                        "Object " + id + " not found in current choices", true, action, true);
                }
            }
        }

        // Normalize empty mana_plan to null
        if (manaPlanArray != null && manaPlanArray.length == 0) {
            manaPlanArray = null;
        }

        // Auto-populate choices if the model skipped get_action_choices.
        // Must happen BEFORE clearing pendingAction, because getActionChoices() reads it.
        if (index != null && lastChoices == null) {
            logger.info("[" + client.getUsername() + "] choose_action: auto-populating choices (get_action_choices was not called)");
            getActionChoices(null);
        }

        // Clear pending action only if it hasn't been overwritten by a new callback.
        // Without this CAS, a callback arriving between our read and this write would be lost.
        synchronized (actionLock) {
            if (pendingAction == action) {
                pendingAction = null;
            }
        }

        UUID gameId = action.gameId();
        ClientCallbackMethod method = action.method();
        Object data = action.data();

        result.put("success", true);

        try {
            switch (method) {
                case GAME_ASK:
                    // GAME_ASK is boolean-only; ignore index if also provided
                    // (some models send all params with defaults)
                    if (answer == null) {
                        return buildError(result, "missing_param",
                            "GAME_ASK requires choice=\"yes\" or choice=\"no\". "
                            + "This is a yes/no question.", true, action);
                    }
                    if (index != null) {
                        logger.warn("[" + client.getUsername() + "] choose_action: ignoring index=" + index + " for GAME_ASK (boolean-only)");
                    }
                    session.sendPlayerBoolean(gameId, answer);
                    result.put("action_taken", answer ? "yes" : "no");
                    break;

                case GAME_SELECT: {
                    // Support both index (play a card) and answer (pass priority).
                    // When both are provided (some models send all params with defaults),
                    // try index first but fall through to answer if index is invalid.
                    boolean usedIndex = false;
                    if (index != null) {
                        List<Object> choices = lastChoices; // snapshot volatile to prevent TOCTOU race
                        if (choices == null || index < 0 || index >= choices.size()) {
                            logChoiceOutOfRangeDiagnostic(method, index, choices);
                            // Index is invalid — if answer is also available, fall through
                            if (answer != null) {
                                logger.warn("[" + client.getUsername() + "] choose_action: index " + index
                                    + " out of range, falling through to answer=" + answer + " for GAME_SELECT");
                            } else {
                                return buildError(result, "index_out_of_range",
                                    "Index " + index + " is out of range"
                                    + (choices != null ? " (valid: 0-" + (choices.size() - 1) + ")" : " (no choices loaded — call get_action_choices first)")
                                    + ". Call get_action_choices to see current options.", true, action, true);
                            }
                        } else {
                            Object chosen = choices.get(index);
                            if (chosen instanceof UUID) {
                                // Validate mana plan before sending spell to server —
                                // once sent, cancellation is async and confuses the model
                                if (manaPlanArray != null) {
                                    CopyOnWriteArrayList<ManaPlanEntry> parsedPlan;
                                    try {
                                        parsedPlan = parseManaPlan(manaPlanArray);
                                    } catch (IllegalArgumentException e) {
                                        return buildError(result, "invalid_mana_plan",
                                            "Invalid mana_plan: " + e.getMessage()
                                            + ". Expected: [\"p1\",\"p2:0\",\"RED\"]", true, action);
                                    }
                                    for (ManaPlanEntry entry : parsedPlan) {
                                        if ("tap".equals(entry.type()) && shortIds.tryResolve(entry.value()) == null) {
                                            return buildError(result, "invalid_mana_plan",
                                                "Mana plan references unknown permanent '" + entry.value()
                                                + "'. Check the board state for correct permanent IDs.", true, action);
                                        }
                                    }
                                    manaPlan = parsedPlan;
                                    // auto_tap controls fallback when plan runs out:
                                    // false = cancel spell, true/null = fall through to auto-tap
                                    manaPlanAutoTapFallback = !(autoTap != null && !autoTap);
                                    result.put("mana_plan_set", true);
                                    result.put("mana_plan_size", manaPlan.size());
                                } else if (autoTap != null && autoTap) {
                                    manaPlan = null;  // Explicit auto-tap mode
                                    manaPlanAbilityIndex = null;
                                    manaPlanAutoTapFallback = true;
                                }
                                session.sendPlayerUUID(gameId, (UUID) chosen);
                                result.put("action_taken", "selected_" + index);
                                usedIndex = true;
                            } else if (chosen instanceof String) {
                                session.sendPlayerString(gameId, (String) chosen);
                                result.put("action_taken", "special_" + chosen);
                                usedIndex = true;
                            } else {
                                return buildError(result, "internal_error",
                                    "Unexpected choice type at index " + index, false, action);
                            }
                        }
                    }
                    if (!usedIndex) {
                        if (answer != null) {
                            session.sendPlayerBoolean(gameId, answer);
                            result.put("action_taken", answer ? "confirmed" : "passed_priority");
                        } else {
                            return buildError(result, "missing_param",
                                "GAME_SELECT requires choice=pN to play a card, "
                                + "or choice=\"no\" to pass priority. Call get_action_choices first to see available cards.",
                                true, action, true);
                        }
                    }
                    break;
                }

                case GAME_PLAY_MANA:
                case GAME_PLAY_XMANA: {
                    // index = tap a mana source OR spend a mana type from pool, answer=false = cancel.
                    // When both are provided and index is invalid, fall through to answer.
                    boolean usedManaIndex = false;
                    if (index != null) {
                        List<Object> choices = lastChoices; // snapshot volatile to prevent TOCTOU race
                        if (choices == null || index < 0 || index >= choices.size()) {
                            logChoiceOutOfRangeDiagnostic(method, index, choices);
                            if (answer != null && !answer) {
                                logger.warn("[" + client.getUsername() + "] choose_action: index " + index
                                    + " out of range, falling through to cancel for GAME_PLAY_MANA");
                            } else {
                                return buildError(result, "index_out_of_range",
                                    "Index " + index + " is out of range"
                                    + (choices != null ? " (valid: 0-" + (choices.size() - 1) + ")" : " (no choices loaded — call get_action_choices first)")
                                    + ". Call get_action_choices to see current options.", true, action, true);
                            }
                        } else {
                            Object manaChoice = choices.get(index);
                            if (manaChoice instanceof UUID) {
                                session.sendPlayerUUID(gameId, (UUID) manaChoice);
                                result.put("action_taken", "tapped_mana_" + index);
                                usedManaIndex = true;
                            } else if (manaChoice instanceof ManaType) {
                                UUID manaPlayerId = getManaPoolPlayerId(gameId, lastGameView);
                                if (manaPlayerId == null) {
                                    return buildError(result, "internal_error",
                                        "Could not resolve player ID for mana pool selection", false, action);
                                }
                                ManaType manaType = (ManaType) manaChoice;
                                session.sendPlayerManaType(gameId, manaPlayerId, manaType);
                                result.put("action_taken", "used_pool_" + manaType.toString());
                                usedManaIndex = true;
                            } else {
                                return buildError(result, "internal_error",
                                    "Unsupported mana choice type at index " + index, false, action);
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
                            session.sendPlayerBoolean(gameId, false);
                            result.put("action_taken", "cancelled_spell");
                        } else {
                            return buildError(result, "missing_param",
                                "GAME_PLAY_MANA requires choice=pN to choose a mana source, or choice=\"no\" to cancel the spell. "
                                + "Call get_action_choices first to see available mana sources.", true, action, true);
                        }
                    }
                    break;
                }

                case GAME_TARGET: {
                    GameClientMessage targetMsg = (GameClientMessage) data;
                    boolean required = targetMsg.isFlag();

                    // Index takes priority over answer:false (models sometimes send both)
                    if (index != null) {
                        if (answer != null) {
                            logger.warn("[" + client.getUsername() + "] choose_action: ignoring answer=" + answer + " because index was also provided for GAME_TARGET");
                        }
                        List<Object> choices = lastChoices; // snapshot volatile to prevent TOCTOU race
                        if (choices != null && index >= 0 && index < choices.size()) {
                            UUID targetUUID = (UUID) choices.get(index);
                            session.sendPlayerUUID(gameId, targetUUID);
                            result.put("action_taken", "selected_target_" + index);
                            break;
                        }
                        logChoiceOutOfRangeDiagnostic(method, index, choices);
                        // Index out of range. For required targets, auto-select to avoid
                        // infinite retry loops. For optional targets, return an error so
                        // the model can retry with a valid index or answer=false.
                        if (!required) {
                            List<Object> targetChoices = lastChoices;
                            return buildError(result, "index_out_of_range",
                                "Index " + index + " is out of range"
                                + (targetChoices != null ? " (valid: 0-" + (targetChoices.size() - 1) + ")" : " (no choices loaded — call get_action_choices first)")
                                + ". Call get_action_choices to see current targets.", true, action, true);
                        }
                        logger.warn("[" + client.getUsername() + "] choose_action: index " + index
                            + " out of range for required GAME_TARGET (choices="
                            + (choices == null ? "null" : choices.size()) + "), auto-selecting");
                    } else if (answer != null && !answer) {
                        // Explicit cancel via answer=false
                        if (!required) {
                            session.sendPlayerBoolean(gameId, false);
                            result.put("action_taken", "cancelled");
                            break;
                        }
                        // Required target — can't cancel, fall through to auto-select
                        logger.warn("[" + client.getUsername() + "] choose_action: answer=false invalid for required GAME_TARGET, auto-selecting");
                    } else if (!required) {
                        // No index, no answer=false — return error for optional targets
                        return buildError(result, "missing_param",
                            "GAME_TARGET requires choice=pN to select a target, or choice=\"no\" to cancel targeting. "
                            + "Call get_action_choices first to see available targets.", true, action, true);
                    }

                    // Auto-select for required targets when index was invalid/missing
                    Set<UUID> autoTargets = findValidTargets(targetMsg);
                    if (autoTargets != null && !autoTargets.isEmpty()) {
                        UUID firstTarget = selectDeterministicTarget(autoTargets, lastChoices);
                        logger.warn("[" + client.getUsername() + "] choose_action: auto-selecting first target for required GAME_TARGET");
                        session.sendPlayerUUID(gameId, firstTarget);
                        result.put("action_taken", "auto_selected_required_target");
                        result.put("warning", "Required target auto-selected. Use get_action_choices first, then index=N.");
                    } else {
                        logger.error("[" + client.getUsername() + "] Required GAME_TARGET has no valid targets — cancelling to avoid infinite loop");
                        session.sendPlayerBoolean(gameId, false);
                        result.put("action_taken", "cancelled_no_valid_targets");
                    }
                    break;
                }

                case GAME_CHOOSE_ABILITY: {
                    if (index == null) {
                        return buildError(result, "missing_param",
                            "GAME_CHOOSE_ABILITY requires index=N. Call get_action_choices first to see "
                            + "the available abilities, then choose_action with the index of the one you want.",
                            true, action, true);
                    }
                    List<Object> abilityChoices = lastChoices; // snapshot volatile to prevent TOCTOU race
                    if (abilityChoices == null || index < 0 || index >= abilityChoices.size()) {
                        logChoiceOutOfRangeDiagnostic(method, index, abilityChoices);
                        return buildError(result, "index_out_of_range",
                            "Index " + index + " is out of range"
                            + (abilityChoices != null ? " (valid: 0-" + (abilityChoices.size() - 1) + ")" : " (no choices loaded — call get_action_choices first)")
                            + ". Call get_action_choices to see current options.", true, action, true);
                    }
                    UUID abilityUUID = (UUID) abilityChoices.get(index);
                    session.sendPlayerUUID(gameId, abilityUUID);
                    result.put("action_taken", "selected_ability_" + index);
                    break;
                }

                case GAME_CHOOSE_CHOICE: {
                    // Support text parameter for choosing by name (e.g. creature type not in filtered list)
                    if (text != null && !text.isEmpty()) {
                        GameClientMessage choiceMsg = (GameClientMessage) data;
                        Choice choiceObj = choiceMsg.getChoice();
                        if (choiceObj == null) {
                            return buildError(result, "internal_error", "No choice available", false, action);
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
                                return buildError(result, "invalid_choice",
                                    "'" + text + "' is not a valid choice", true, action, true);
                            }
                            session.sendPlayerString(gameId, matchedKey);
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
                                return buildError(result, "invalid_choice",
                                    "'" + text + "' is not a valid choice", true, action, true);
                            }
                            session.sendPlayerString(gameId, matched);
                        }
                        result.put("action_taken", "selected_choice_text_" + text);
                        break;
                    }
                    if (index == null) {
                        return buildError(result, "missing_param",
                            "Integer 'index' or string 'text' required for GAME_CHOOSE_CHOICE", true, action, true);
                    }
                    List<Object> choiceChoices = lastChoices; // snapshot volatile to prevent TOCTOU race
                    if (choiceChoices == null || index < 0 || index >= choiceChoices.size()) {
                        logChoiceOutOfRangeDiagnostic(method, index, choiceChoices);
                        return buildError(result, "index_out_of_range",
                            "Index " + index + " is out of range"
                            + (choiceChoices != null ? " (valid: 0-" + (choiceChoices.size() - 1) + ")" : " (no choices loaded — call get_action_choices first)")
                            + ". Call get_action_choices to see current options.", true, action, true);
                    }
                    String choiceStr = (String) choiceChoices.get(index);
                    session.sendPlayerString(gameId, choiceStr);
                    result.put("action_taken", "selected_choice_" + index);
                    break;
                }

                case GAME_CHOOSE_PILE:
                    if (pile == null) {
                        return buildError(result, "missing_param",
                            "Integer 'pile' (1 or 2) required for GAME_CHOOSE_PILE", true, action);
                    }
                    boolean pileChoice = pile == 1;
                    session.sendPlayerBoolean(gameId, pileChoice);
                    result.put("action_taken", "selected_pile_" + pile);
                    break;

                case GAME_GET_AMOUNT: {
                    if (amount == null) {
                        return buildError(result, "missing_param",
                            "Integer 'amount' required for GAME_GET_AMOUNT", true, action);
                    }
                    GameClientMessage msg = (GameClientMessage) data;
                    int clamped = Math.max(msg.getMin(), Math.min(msg.getMax(), amount));
                    session.sendPlayerInteger(gameId, clamped);
                    result.put("action_taken", "amount_" + clamped);
                    break;
                }

                case GAME_GET_MULTI_AMOUNT: {
                    if (amounts == null) {
                        return buildError(result, "missing_param",
                            "Array 'amounts' required for GAME_GET_MULTI_AMOUNT", true, action);
                    }
                    var sb = new StringBuilder();
                    for (int i = 0; i < amounts.length; i++) {
                        if (i > 0) sb.append(" ");
                        sb.append(amounts[i]);
                    }
                    String multiAmountStr = sb.toString();
                    session.sendPlayerString(gameId, multiAmountStr);
                    result.put("action_taken", "multi_amount");
                    break;
                }

                default:
                    buildError(result, "unknown_action_type", "Unknown action type: " + method, false, null);
            }
        } finally {
            lastChoices = null;
            if (Boolean.FALSE.equals(result.get("success"))) {
                logger.warn("[" + client.getUsername() + "] choose_action failed: " + result.get("error"));
            }
        }

        // After successful action, wait for next pending action before returning.
        // This prevents the LLM from waking up to an empty state.
        if (Boolean.TRUE.equals(result.get("success"))) {
            long waitStart = System.currentTimeMillis();
            logger.debug("[" + client.getUsername() + "] chooseAction: waiting for next callback (max " + POST_ACTION_WAIT_MS + "ms)");
            while (pendingAction == null) {
                if (playerDead || (activeGames.isEmpty() && gameEverStarted) || !client.isRunning()) {
                    break;
                }
                if (System.currentTimeMillis() - waitStart > POST_ACTION_WAIT_MS) {
                    break;
                }
                synchronized (actionLock) {
                    try {
                        actionLock.wait(200);
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                        break;
                    }
                }
            }
            PendingAction next = pendingAction;
            long waitElapsed = System.currentTimeMillis() - waitStart;
            if (next != null) {
                logger.debug("[" + client.getUsername() + "] chooseAction: next callback arrived after " + waitElapsed + "ms");
            } else {
                logger.info("[" + client.getUsername() + "] chooseAction: next callback NOT arrived after " + waitElapsed + "ms");
            }
            if (next != null) {
                result.put("next_action_pending", true);
                result.put("next_action_type", next.method().name());
                String nextMsg = stripHtml(next.message());
                if (nextMsg != null && !nextMsg.isEmpty()) {
                    result.put("next_action_message", nextMsg);
                }
                result.put("next_action_hint", "Call get_action_choices or choose_action to see details, or pass_priority to continue.");
            }
        }

        return result;
    }

    // ── Batch combat ──────────────────────────────────────────────────────

    /**
     * Wait for the next pending action callback from the server.
     * Used internally by batch combat to chain multiple send→wait cycles.
     * Returns the new PendingAction, or null on timeout.
     */
    private PendingAction waitForNextCallback(UUID gameId) {
        long waitStart = System.currentTimeMillis();
        while (true) {
            PendingAction next = pendingAction;
            if (next != null) {
                return next;
            }
            if (playerDead || (activeGames.isEmpty() && gameEverStarted) || !client.isRunning()) {
                return null;
            }
            if (System.currentTimeMillis() - waitStart > 10_000) {
                logger.warn("[" + client.getUsername() + "] waitForNextCallback: timed out after 10s");
                return null;
            }
            synchronized (actionLock) {
                try {
                    actionLock.wait(200);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return null;
                }
            }
        }
    }

    /**
     * Declare multiple attackers in one batch.
     * Sends each attacker UUID, waits for the next GAME_SELECT, then confirms.
     * Special case: attackers=["all"] sends the "special" all-attack button.
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> handleBatchAttackers(String[] attackerIds, PendingAction action, Map<String, Object> result) {
        UUID gameId = action.gameId();
        var declared = new ArrayList<String>();
        var failed = new ArrayList<Map<String, Object>>();

        // Special case: "all" attack
        if (attackerIds.length == 1 && "all".equals(attackerIds[0])) {
            synchronized (actionLock) {
                if (pendingAction == action) {
                    pendingAction = null;
                }
            }
            session.sendPlayerString(gameId, "special");
            // Wait for next callback (server will send a new GAME_SELECT to confirm)
            PendingAction next = waitForNextCallback(gameId);
            if (next != null && next.method() == ClientCallbackMethod.GAME_SELECT) {
                synchronized (actionLock) {
                    if (pendingAction == next) {
                        pendingAction = null;
                    }
                }
                session.sendPlayerBoolean(gameId, true);
            }
            result.put("success", true);
            result.put("action_taken", "batch_attack");
            declared.add("all");
            result.put("declared", declared);
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
            session.sendPlayerUUID(gameId, attackerUuid);
            declared.add(shortId);

            // Wait for next callback
            PendingAction next = waitForNextCallback(gameId);
            if (next == null) {
                result.put("interrupted", true);
                break;
            }
            if (next.method() != ClientCallbackMethod.GAME_SELECT) {
                // Interrupted by a trigger or other callback
                result.put("interrupted", true);
                break;
            }
            // Update possibleAttackers from the new callback for validation
            if (next.data() instanceof GameClientMessage) {
                GameClientMessage nextGcm = (GameClientMessage) next.data();
                Map<String, Serializable> nextOptions = nextGcm.getOptions();
                if (nextOptions != null && nextOptions.containsKey("possibleAttackers")) {
                    possibleAttackerUuids = (List<UUID>) nextOptions.get("possibleAttackers");
                }
            }
        }

        // Confirm attackers (send true)
        if (!Boolean.TRUE.equals(result.get("interrupted"))) {
            synchronized (actionLock) {
                if (pendingAction != null) {
                    pendingAction = null;
                }
            }
            session.sendPlayerBoolean(gameId, true);
        }

        result.put("success", true);
        result.put("action_taken", "batch_attack");
        result.put("declared", declared);
        if (!failed.isEmpty()) {
            result.put("failed", failed);
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
    private Map<String, Object> handleBatchBlockers(String[] blockersArray, PendingAction action, Map<String, Object> result) {
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
            session.sendPlayerUUID(gameId, blockerUuid);

            // Wait for next callback — could be GAME_TARGET (pick which attacker)
            // or GAME_SELECT (single attacker, auto-assigned)
            PendingAction next = waitForNextCallback(gameId);
            if (next == null) {
                result.put("interrupted", true);
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
                    session.sendPlayerBoolean(gameId, false);
                    next = waitForNextCallback(gameId);
                    if (next == null || next.method() != ClientCallbackMethod.GAME_SELECT) {
                        result.put("interrupted", true);
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
                    session.sendPlayerBoolean(gameId, false);
                    next = waitForNextCallback(gameId);
                    if (next == null || next.method() != ClientCallbackMethod.GAME_SELECT) {
                        result.put("interrupted", true);
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
                session.sendPlayerUUID(gameId, attackerUuid);
                declared.add(Map.of("id", blockerShortId, "blocks", attackerShortId));

                // Wait for next GAME_SELECT (back to blocker selection)
                next = waitForNextCallback(gameId);
                if (next == null || next.method() != ClientCallbackMethod.GAME_SELECT) {
                    result.put("interrupted", true);
                    break;
                }

                // Update possibleBlockers from the new callback
                if (next.data() instanceof GameClientMessage) {
                    GameClientMessage nextGcm = (GameClientMessage) next.data();
                    Map<String, Serializable> nextOptions = nextGcm.getOptions();
                    if (nextOptions != null && nextOptions.containsKey("possibleBlockers")) {
                        possibleBlockerUuids = (List<UUID>) nextOptions.get("possibleBlockers");
                    }
                }
            } else if (next.method() == ClientCallbackMethod.GAME_SELECT) {
                // Single attacker — auto-assigned by the server (lines 3010-3024 in handleCallback)
                declared.add(Map.of("id", blockerShortId, "blocks", attackerShortId));

                // Update possibleBlockers from the new callback
                if (next.data() instanceof GameClientMessage) {
                    GameClientMessage nextGcm = (GameClientMessage) next.data();
                    Map<String, Serializable> nextOptions = nextGcm.getOptions();
                    if (nextOptions != null && nextOptions.containsKey("possibleBlockers")) {
                        possibleBlockerUuids = (List<UUID>) nextOptions.get("possibleBlockers");
                    }
                }
            } else {
                // Interrupted by unexpected callback
                result.put("interrupted", true);
                break;
            }
        }

        // Confirm blockers (send true)
        if (!Boolean.TRUE.equals(result.get("interrupted"))) {
            synchronized (actionLock) {
                if (pendingAction != null) {
                    pendingAction = null;
                }
            }
            session.sendPlayerBoolean(gameId, true);
        }

        result.put("success", true);
        result.put("action_taken", "batch_block");
        result.put("declared", declared);
        if (!failed.isEmpty()) {
            result.put("failed", failed);
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
     * After batch combat, wait for the next pending action before returning.
     * Similar to the post-chooseAction wait, but factored out for reuse.
     */
    private void waitForNextActionAfterBatch(Map<String, Object> result) {
        long waitStart = System.currentTimeMillis();
        while (pendingAction == null) {
            if (playerDead || (activeGames.isEmpty() && gameEverStarted) || !client.isRunning()) {
                break;
            }
            if (System.currentTimeMillis() - waitStart > POST_ACTION_WAIT_MS) {
                break;
            }
            synchronized (actionLock) {
                try {
                    actionLock.wait(200);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    break;
                }
            }
        }
        PendingAction next = pendingAction;
        if (next != null) {
            result.put("next_action_pending", true);
            result.put("next_action_type", next.method().name());
            String nextMsg = stripHtml(next.message());
            if (nextMsg != null && !nextMsg.isEmpty()) {
                result.put("next_action_message", nextMsg);
            }
            result.put("next_action_hint", "Call get_action_choices or choose_action to see details, or pass_priority to continue.");
        }
    }

    // ── End batch combat ──────────────────────────────────────────────────

    private String describeTarget(UUID targetId, CardsView cardsView, GameView gameView) {
        GameView view = gameView != null ? gameView : lastGameView;
        // Try cardsView first (cards presented in the targeting UI)
        if (cardsView != null) {
            CardView cv = cardsView.get(targetId);
            if (cv != null) {
                return buildCardDescription(cv) + controllerSuffix(targetId, view);
            }
        }
        // Fall back to game state lookup
        CardView cv = findCardViewById(targetId, view);
        if (cv != null) {
            return buildCardDescription(cv) + controllerSuffix(targetId, view);
        }
        // Check if the target is a player
        if (view != null) {
            UUID gameId = currentGameId; // snapshot volatile to prevent TOCTOU race
            UUID myPlayerId = gameId != null ? activeGames.get(gameId) : null;
            for (PlayerView player : view.getPlayers()) {
                if (player.getPlayerId().equals(targetId)) {
                    String desc = player.getName();
                    if (player.getPlayerId().equals(myPlayerId)) {
                        desc += " (you)";
                    }
                    return desc;
                }
            }
        }
        return "Unknown (" + targetId.toString().substring(0, 8) + ")";
    }

    /**
     * Populate a choice entry map with structured target fields: name, target_type,
     * is_you, controller, power, toughness, tapped.
     */
    private void buildTargetInfo(Map<String, Object> entry, UUID targetId,
                                  CardsView cardsView, GameView gameView, UUID myPlayerId) {
        // Try cardsView first (cards presented in the targeting UI)
        CardView cv = null;
        if (cardsView != null) {
            cv = cardsView.get(targetId);
        }
        if (cv == null) {
            cv = findCardViewById(targetId, gameView);
        }
        if (cv != null) {
            entry.put("name", safeDisplayName(cv));
            if (cv instanceof PermanentView) {
                entry.put("target_type", "permanent");
                PermanentView pv = (PermanentView) cv;
                if (pv.isCreature() && cv.getPower() != null) {
                    entry.put("power", cv.getPower());
                    entry.put("toughness", cv.getToughness());
                }
                if (pv.isTapped()) {
                    entry.put("tapped", true);
                }
            } else {
                entry.put("target_type", "card");
            }
            // Add controller info for permanents on the battlefield
            if (gameView != null) {
                for (PlayerView player : gameView.getPlayers()) {
                    if (player.getBattlefield().get(targetId) != null) {
                        if (!player.getPlayerId().equals(myPlayerId)) {
                            entry.put("controller", player.getName());
                        }
                        break;
                    }
                }
            }
            return;
        }
        // Check if the target is a player
        if (gameView != null) {
            for (PlayerView player : gameView.getPlayers()) {
                if (player.getPlayerId().equals(targetId)) {
                    entry.put("name", player.getName());
                    entry.put("target_type", "player");
                    if (player.getPlayerId().equals(myPlayerId)) {
                        entry.put("is_you", true);
                    }
                    return;
                }
            }
        }
        entry.put("name", "Unknown (" + targetId.toString().substring(0, 8) + ")");
        entry.put("target_type", "card");
    }

    /**
     * Return a suffix like " (yours)" or " (PlayerName's)" indicating who controls
     * the permanent with the given ID. Returns "" if not found on any battlefield.
     */
    private String controllerSuffix(UUID objectId) {
        return controllerSuffix(objectId, lastGameView);
    }

    private String controllerSuffix(UUID objectId, GameView gameView) {
        if (gameView == null) return "";
        UUID gameId = currentGameId;
        UUID myPlayerId = gameId != null ? activeGames.get(gameId) : null;
        for (PlayerView player : gameView.getPlayers()) {
            if (player.getBattlefield().get(objectId) != null) {
                if (player.getPlayerId().equals(myPlayerId)) {
                    return " (yours)";
                } else {
                    return " (" + player.getName() + "'s)";
                }
            }
        }
        return "";
    }

    private String safeDisplayName(CardView cv) {
        String name = cv.getDisplayName();
        if (name == null) {
            name = cv.getName() != null ? cv.getName() : "Unknown";
        }
        return name;
    }

    /**
     * Build a structured info map for a card: name, mana_cost, is_land, power/toughness, rules.
     * Used for hand cards, pile decisions, and mulligan hands.
     */
    private Map<String, Object> buildCardInfoMap(CardView cv) {
        var info = new HashMap<String, Object>();
        info.put("name", safeDisplayName(cv));
        String manaCost = cv.getManaCostStr();
        if (manaCost != null && !manaCost.isEmpty()) {
            info.put("mana_cost", manaCost);
        }
        if (cv.isLand()) {
            info.put("is_land", true);
        }
        if (cv.isCreature() && cv.getPower() != null) {
            info.put("power", cv.getPower());
            info.put("toughness", cv.getToughness());
        }
        List<String> rules = stripHtmlList(cv.getRules());
        if (rules != null && !rules.isEmpty()) {
            info.put("rules", rules);
        }
        return info;
    }

    private String buildCardDescription(CardView cv) {
        String displayName = cv.getDisplayName();
        if (displayName == null) {
            displayName = cv.getName() != null ? cv.getName() : "Unknown";
        }
        var sb = new StringBuilder(displayName);
        if (cv instanceof PermanentView) {
            PermanentView pv = (PermanentView) cv;
            if (pv.isCreature() && cv.getPower() != null && cv.getToughness() != null) {
                sb.append(" (").append(cv.getPower()).append("/").append(cv.getToughness()).append(")");
            }
            if (pv.isTapped()) {
                sb.append(" [tapped]");
            }
        }
        return sb.toString();
    }

    public String getGameLog(int maxChars) {
        synchronized (gameLog) {
            if (maxChars <= 0 || maxChars >= gameLog.length()) {
                return gameLog.toString();
            }
            return gameLog.substring(gameLog.length() - maxChars);
        }
    }

    public int getGameLogLength() {
        synchronized (gameLog) {
            return gameLog.length() + gameLogTrimmedChars;
        }
    }

    private int getGameLogOldestOffset() {
        synchronized (gameLog) {
            return gameLogTrimmedChars;
        }
    }

    public Map<String, Object> getGameLogChunk(int maxChars, Integer cursor) {
        var result = new HashMap<String, Object>();
        int totalLength = getGameLogLength();
        if (cursor != null) {
            int oldestOffset = getGameLogOldestOffset();
            int requestedOffset = cursor;
            int effectiveOffset = Math.max(requestedOffset, oldestOffset);
            effectiveOffset = Math.min(effectiveOffset, totalLength);
            result.put("log", stripHtml(getGameLogSince(effectiveOffset)));
            result.put("total_length", totalLength);
            result.put("truncated", requestedOffset < oldestOffset);
            result.put("cursor", totalLength);
            if (requestedOffset < oldestOffset) {
                result.put("cursor_reset", true);
            }
            return result;
        }

        String rawLog = getGameLog(maxChars);
        result.put("log", stripHtml(rawLog));
        result.put("total_length", totalLength);
        result.put("truncated", rawLog.length() < totalLength);
        result.put("cursor", totalLength);
        return result;
    }

    /**
     * Return game log entries starting from a specific player's Nth turn.
     * Scans for "{player} turn {sinceTurn}" marker in the log.
     * If player is null, defaults to this client's player name.
     */
    public Map<String, Object> getGameLogSinceTurn(String player, int sinceTurn) {
        if (player == null) {
            player = client.getUsername();
        }
        var result = new HashMap<String, Object>();
        int totalLength = getGameLogLength();
        String marker = player + " turn " + sinceTurn;

        synchronized (gameLog) {
            String logStr = gameLog.toString();
            // Search for the marker at start of line (after newline or at position 0)
            int startPos = -1;
            if (logStr.startsWith(marker)) {
                startPos = 0;
            } else {
                int idx = logStr.indexOf("\n" + marker);
                if (idx >= 0) {
                    startPos = idx + 1; // skip the newline
                }
            }

            if (startPos >= 0) {
                result.put("log", stripHtml(logStr.substring(startPos)));
                result.put("truncated", false);
                result.put("since_turn", sinceTurn);
                result.put("since_player", player);
            } else {
                // Marker not found: either trimmed (too old) or hasn't happened yet
                Integer currentTurn = playerTurnCounts.get(player);
                if (currentTurn != null && sinceTurn <= currentTurn && !logStr.isEmpty()) {
                    // Turn existed but was trimmed from the buffer
                    result.put("log", stripHtml(logStr));
                    result.put("truncated", true);
                    result.put("since_player", player);
                } else {
                    // Turn hasn't happened yet or player not found
                    result.put("log", "");
                    result.put("truncated", false);
                }
            }

            result.put("total_length", totalLength);
            result.put("cursor", totalLength);
        }
        return result;
    }

    /**
     * Pull new bridge events from the server since our last cursor.
     * Returns the list of new events and advances the cursor.
     */
    private List<BridgeLogEntry> pullBridgeEvents() {
        UUID gameId = currentGameId;
        if (gameId == null) return List.of();
        UUID playerId = activeGames.get(gameId);
        if (playerId == null) return List.of();
        try {
            List<BridgeLogEntry> events = session.getBridgeEvents(gameId, playerId, bridgeEventCursor);
            if (events != null && !events.isEmpty()) {
                bridgeEventCursor = events.get(events.size() - 1).index() + 1;
                cachedBridgeEvents.addAll(events);
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
    public Map<String, Object> getGameHistory(Integer sinceTurn, Integer sinceCursor) {
        // Try pulling fresh events from the server
        int savedCursor = bridgeEventCursor;
        if (sinceCursor != null) {
            bridgeEventCursor = sinceCursor;
        } else {
            bridgeEventCursor = 0;
        }

        List<BridgeLogEntry> events = pullBridgeEvents();
        int newCursor = bridgeEventCursor;

        // Restore cursor
        bridgeEventCursor = savedCursor;

        // If the server returned nothing (game ended, controller cleaned up),
        // fall back to cached events from earlier pulls or handleGameOver.
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

        if (events.isEmpty()) {
            var result = new HashMap<String, Object>();
            result.put("history", "No game events recorded yet.");
            result.put("cursor", newCursor);
            result.put("event_count", 0);
            return result;
        }

        // Group events by turn, then by phase+step
        StringBuilder sb = new StringBuilder();
        int currentTurn = -1;
        String currentPhaseStep = null;

        for (BridgeLogEntry entry : events) {
            // Turn header
            if (entry.turn() != currentTurn) {
                currentTurn = entry.turn();
                currentPhaseStep = null;
                if (sb.length() > 0) sb.append("\n");
                sb.append("Turn ").append(currentTurn);
                if (entry.activePlayer() != null) {
                    sb.append(" (").append(entry.activePlayer()).append(")");
                }
                sb.append(":\n");
            }

            // Phase/step sub-header
            String phaseStep = formatPhaseStep(entry.phase(), entry.step());
            if (phaseStep != null && !phaseStep.equals(currentPhaseStep)) {
                currentPhaseStep = phaseStep;
                sb.append("  ").append(phaseStep).append(":\n");
            }

            // Event description
            String desc = formatBridgeEvent(entry);
            if (desc != null) {
                sb.append("    - ").append(desc).append("\n");
            }
        }

        var result = new HashMap<String, Object>();
        result.put("history", sb.toString());
        result.put("cursor", newCursor);
        result.put("event_count", events.size());
        return result;
    }

    /** Format a phase+step pair into a human-readable header. */
    private static String formatPhaseStep(String phase, String step) {
        if (phase == null && step == null) return null;
        if (step != null) {
            return switch (step) {
                case "UPKEEP" -> "Upkeep";
                case "DRAW" -> "Draw";
                case "PRECOMBAT_MAIN" -> "Precombat Main";
                case "BEGIN_COMBAT" -> "Begin Combat";
                case "DECLARE_ATTACKERS" -> "Declare Attackers";
                case "DECLARE_BLOCKERS" -> "Declare Blockers";
                case "FIRST_COMBAT_DAMAGE", "COMBAT_DAMAGE" -> "Combat Damage";
                case "END_COMBAT" -> "End Combat";
                case "POSTCOMBAT_MAIN" -> "Postcombat Main";
                case "END_TURN" -> "End Step";
                case "CLEANUP" -> "Cleanup";
                default -> step.replace('_', ' ').toLowerCase();
            };
        }
        return phase.replace('_', ' ').toLowerCase();
    }

    /** Format a single BridgeLogEntry into a human-readable action description. */
    private static String formatBridgeEvent(BridgeLogEntry entry) {
        String player = entry.player();
        String card = entry.cardName();
        String target = entry.targetName();
        int amount = entry.amount();

        return switch (entry.type()) {
            case "SPELL_CAST" -> player + " cast " + (card != null ? card : "a spell")
                    + (target != null ? " targeting " + target : "");
            case "LAND_PLAYED" -> player + " played " + (card != null ? card : "a land");
            case "ACTIVATED_ABILITY" -> player + " activated "
                    + (card != null ? card + "'s ability" : "an ability")
                    + (target != null ? " targeting " + target : "");
            case "ATTACKER_DECLARED" -> player + " attacked with " + (card != null ? card : "a creature")
                    + (target != null ? " (attacking " + target + ")" : "");
            case "BLOCKER_DECLARED" -> player + " blocked"
                    + (target != null ? " " + target : "")
                    + (card != null ? " with " + card : "");
            case "DESTROYED_PERMANENT" -> (card != null ? card : "A permanent") + " was destroyed"
                    + (player != null ? " (" + player + ")" : "");
            case "SACRIFICED_PERMANENT" -> player + " sacrificed " + (card != null ? card : "a permanent");
            case "COUNTERED" -> (card != null ? card : "A spell") + " was countered"
                    + (target != null ? " (targeting " + target + ")" : "");
            case "GAINED_LIFE" -> player + " gained " + amount + " life";
            case "LOST_LIFE" -> player + " lost " + amount + " life";
            case "DREW_CARD" -> player + " drew"
                    + (card != null ? " " + card : " a card");
            case "BEGIN_TURN" -> null; // Handled by turn header
            default -> entry.type() + (player != null ? " by " + player : "")
                    + (card != null ? " (" + card + ")" : "");
        };
    }

    /**
     * Send a chat message. Returns null on success, or an error string on failure.
     */
    public String sendChatMessage(String message) {
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
     * opponent (potato) may still be in handleGameOver pulling bridge events
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
                boolean finished = gameFinishedLatch.await(15, java.util.concurrent.TimeUnit.SECONDS);
                if (!finished) {
                    logger.warn("[" + client.getUsername() + "] Concede sent but GAME_OVER not received within 15s");
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

    // Cross-turn yield values handled client-side.  These used to be server-side
    // yields (sendPlayerAction → skip()), but skip() bypasses waitResponseOpen()
    // which causes stale responses to answer the wrong waitForResponse(), producing
    // nondeterministic auto-passes.  Client-side handling eliminates the race.
    private static final Set<String> CLIENT_SIDE_YIELDS = Set.of(
        "end_of_turn", "stack_resolved", "my_turn"
    );

    // Mapping from "until" parameter values to PhaseStep enum constants (client-side yield).
    // Only steps where players normally receive priority are exposed.
    private static final Map<String, PhaseStep> STEP_PHASES = Map.of(
        "upkeep", PhaseStep.UPKEEP,
        "draw", PhaseStep.DRAW,
        "precombat_main", PhaseStep.PRECOMBAT_MAIN,
        "begin_combat", PhaseStep.BEGIN_COMBAT,
        "declare_attackers", PhaseStep.DECLARE_ATTACKERS,
        "declare_blockers", PhaseStep.DECLARE_BLOCKERS,
        "end_combat", PhaseStep.END_COMBAT,
        "postcombat_main", PhaseStep.POSTCOMBAT_MAIN
    );

    /**
     * Merge action choices into a pass_priority result so the LLM gets choices
     * without a separate get_action_choices round-trip.
     */
    private void mergeActionChoices(Map<String, Object> result, Long boardCursorParam) {
        Map<String, Object> choices = getActionChoices(boardCursorParam);
        if (!Boolean.TRUE.equals(choices.get("action_pending"))) {
            // Rare race: action disappeared between pass_priority detecting it
            // and getActionChoices() fetching it.
            result.put("warning", "Action changed before choices were fetched");
            return;
        }
        // Merge all choice fields into the result.  pass_priority fields
        // (action_pending, stop_reason, etc.) are already set
        // and take precedence — only copy fields the result doesn't have yet.
        for (Map.Entry<String, Object> entry : choices.entrySet()) {
            result.putIfAbsent(entry.getKey(), entry.getValue());
        }
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
    public Map<String, Object> passPriority(String until, Long boardCursorParam) {
        interactionsThisTurn++;

        int actionsPassed = 0;

        // Route the "until" parameter: check step phases first, then cross-turn yields
        boolean yieldActive = false;
        PhaseStep targetStep = null;
        boolean yieldUntilMyTurn = false;
        boolean yieldUntilStackResolved = false;
        // end_of_turn needs no flag — falls through to the playable-cards check
        int yieldStartTurn = lastTurnNumber;
        if (until != null) {
            targetStep = STEP_PHASES.get(until);
            if (targetStep != null) {
                // Client-side step yield: do NOT sendPlayerAction.
                yieldActive = true;
            } else if (CLIENT_SIDE_YIELDS.contains(until)) {
                UUID gameId = currentGameId;
                if (gameId == null) {
                    var result = new HashMap<String, Object>();
                    result.put("error", "No active game for yield");
                    return result;
                }
                // If the pending action is non-priority (e.g. GAME_TARGET for
                // target selection after casting a spell), we must NOT auto-pass
                // it — sendPlayerBoolean(false) would cancel the targeting and
                // fizzle the spell.  Return the pending choices instead, matching
                // the guard at the top of the main loop.
                // This guard must run BEFORE the stack_resolved fast-path below,
                // which otherwise returns early with stop_reason="stack_resolved"
                // instead of "non_priority_action" when the stack is empty.
                PendingAction currentAction;
                synchronized (actionLock) {
                    currentAction = pendingAction;
                }
                if (currentAction != null
                        && currentAction.method() != ClientCallbackMethod.GAME_SELECT) {
                    logger.info("[" + client.getUsername()
                        + "] passPriority: until=" + until
                        + " blocked by pending " + currentAction.method()
                        + " — returning choices instead of auto-passing");
                    var result = new HashMap<String, Object>();
                    result.put("action_pending", true);
                    result.put("action_type", currentAction.method().name());
                    result.put("stop_reason", "non_priority_action");
                    attachUnseenChat(result);
                    mergeActionChoices(result, boardCursorParam);
                    return result;
                }
                // For stack_resolved: if stack is already empty, return immediately
                // (matching server-side behavior that does nothing on empty stack).
                if ("stack_resolved".equals(until)) {
                    GameView gv = lastGameView;
                    if (gv != null && gv.getStack().isEmpty()) {
                        var result = new HashMap<String, Object>();
                        result.put("action_pending", currentAction != null);
                        result.put("stop_reason", "stack_resolved");
                        attachUnseenChat(result);
                        if (currentAction != null) {
                            mergeActionChoices(result, boardCursorParam);
                        }
                        return result;
                    }
                    yieldUntilStackResolved = true;
                } else if ("my_turn".equals(until)) {
                    yieldUntilMyTurn = true;
                }
                // Auto-pass the current priority locally via sendPlayerBoolean
                // instead of sendPlayerAction+skip().  This avoids the race where
                // skip() bypasses waitResponseOpen() and stale responses answer
                // the wrong waitForResponse().
                synchronized (actionLock) {
                    pendingAction = null;
                }
                session.sendPlayerBoolean(gameId, false);
                // The yield consumed the current priority — count it as a pass.
                actionsPassed++;
                yieldActive = true;
            } else {
                var allValues = new java.util.ArrayList<>(STEP_PHASES.keySet());
                allValues.addAll(CLIENT_SIDE_YIELDS);
                var result = new HashMap<String, Object>();
                result.put("error", "Invalid until value: " + until
                    + ". Valid values: " + String.join(", ", allValues));
                return result;
            }
        }

        long startTime = System.currentTimeMillis();
        long lastProgressLogAt = startTime;
        int waitLoops = 0;
        logger.info("[" + client.getUsername() + "] passPriority ENTER: until=" + until
            + " yieldActive=" + yieldActive
            + " pendingAction=" + (pendingAction != null)
            + " activeGames=" + activeGames.size()
            + " lastActionableCallbackAt=" + lastActionableCallbackAt);

        while (true) {
            PendingAction action = pendingAction;
            if (action != null) {
                ClientCallbackMethod method = action.method();

                // Update game view and reset loop counter on turn change.
                // This MUST run before the loop detection check below, otherwise
                // the `continue` in the loop detection branch skips it and the
                // counter never resets, permanently disabling the player.
                // Check any callback carrying GameView, not just GAME_SELECT —
                // a new turn can start with upkeep triggers (GAME_TARGET, GAME_ASK, etc.).
                if (action.data() instanceof GameClientMessage) {
                    GameView gv = ((GameClientMessage) action.data()).getGameView();
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

                // Step-specific yield: turn boundary — target step wasn't reached this turn
                if (targetStep != null && lastTurnNumber != yieldStartTurn) {
                    Map<String, Object> result = new HashMap<>();
                    result.put("action_pending", true);
                    result.put("action_type", method.name());

                    result.put("game_seq", action.gameSeq());
                    GameView gvSnap = lastGameView;
                    if (gvSnap != null) {
                        if (gvSnap.getStep() != null) {
                            result.put("current_step", gvSnap.getStep().toString());
                        }
                    }
                    result.put("stop_reason", "step_not_reached");
                    attachUnseenChat(result);
                    return result;
                }

                // Generic loop detection: too many interactions this turn — auto-pass everything
                if (interactionsThisTurn > maxInteractionsPerTurn) {
                    logger.warn("[" + client.getUsername() + "] Loop detected (" + interactionsThisTurn
                        + " interactions on turn " + lastTurnNumber + "), auto-passing " + method.name());
                    // Not a critical error — LLM is stuck in a loop, not a code bug
                    executeDefaultAction();
                    actionsPassed++;
                    continue;
                }

                // GAME_PLAY_MANA: auto-tapper couldn't handle it, cancel the spell
                if (method == ClientCallbackMethod.GAME_PLAY_MANA || method == ClientCallbackMethod.GAME_PLAY_XMANA) {
                    UUID payingForId = extractPayingForId(action.message());
                    if (payingForId != null) {
                        failedManaCasts.add(payingForId);
                    }
                    synchronized (actionLock) {
                        if (pendingAction == action) {
                            pendingAction = null;
                        }
                    }
                    synchronized (unseenChat) {
                        unseenChat.add("[System] Spell cancelled — not enough mana to complete payment.");
                    }
                    logBridgeEvent("SPELL_CANCELLED", "not enough mana to complete payment");
                    session.sendPlayerBoolean(action.gameId(), false);
                    actionsPassed++;
                    continue;
                }

                // Optional GAME_TARGET with no valid targets: auto-cancel
                if (method == ClientCallbackMethod.GAME_TARGET) {
                    GameClientMessage targetMsg = (GameClientMessage) action.data();
                    boolean required = targetMsg.isFlag();
                    if (!required) {
                        Set<UUID> targets = findValidTargets(targetMsg);
                        if (targets == null || targets.isEmpty()) {
                            synchronized (actionLock) {
                                if (pendingAction == action) {
                                    pendingAction = null;
                                }
                            }
                            session.sendPlayerBoolean(action.gameId(), false);
                            actionsPassed++;
                            continue;
                        }
                    }
                }

                // Non-GAME_SELECT always needs LLM input — return immediately
                if (method != ClientCallbackMethod.GAME_SELECT) {
                    var result = new HashMap<String, Object>();
                    result.put("action_pending", true);
                    result.put("action_type", method.name());

                    result.put("stop_reason", "non_priority_action");
                    attachUnseenChat(result);
                    mergeActionChoices(result, boardCursorParam);
                    return result;
                }

                // Combat selections (declare attackers/blockers) always need LLM input
                String combatType = detectCombatSelect(action);
                if (combatType != null) {
                    var result = new HashMap<String, Object>();
                    result.put("action_pending", true);
                    result.put("action_type", method.name());

                    result.put("combat_phase", combatType);
                    result.put("stop_reason", "combat");
                    attachUnseenChat(result);
                    mergeActionChoices(result, boardCursorParam);
                    return result;
                }

                // Client-side cross-turn yield: my_turn
                // Auto-pass all callbacks during the opponent's turn.  Once it's
                // our turn, clear the flag and fall through to the playable-cards
                // check (which will return if there are meaningful choices).
                if (yieldUntilMyTurn) {
                    GameView gv = (action.data() instanceof GameClientMessage)
                        ? ((GameClientMessage) action.data()).getGameView() : lastGameView;
                    if (gv != null && client.getUsername().equals(gv.getActivePlayerName())) {
                        // We've become the active player — stop yielding
                        yieldUntilMyTurn = false;
                        // Fall through to playable-cards check below
                    } else {
                        // Not our turn — auto-pass
                        synchronized (actionLock) {
                            if (pendingAction == action) {
                                pendingAction = null;
                            }
                        }
                        session.sendPlayerBoolean(action.gameId(), false);
                        actionsPassed++;
                        continue;
                    }
                }

                // Client-side cross-turn yield: stack_resolved
                // Return when the stack becomes empty.  While the stack has items,
                // fall through to the playable-cards check (so we stop for
                // counterspells etc., and auto-pass when we have no responses).
                if (yieldUntilStackResolved) {
                    GameView gv = (action.data() instanceof GameClientMessage)
                        ? ((GameClientMessage) action.data()).getGameView() : lastGameView;
                    if (gv != null && gv.getStack().isEmpty()) {
                        // Stack resolved — return to LLM
                        Map<String, Object> result = new HashMap<>();
                        result.put("action_pending", true);
                        result.put("action_type", method.name());
                        result.put("stop_reason", "stack_resolved");
                        attachUnseenChat(result);
                        mergeActionChoices(result, boardCursorParam);
                        return result;
                    }
                    // Stack still has items — fall through to playable-cards check
                }

                // Step-specific yield: check if we've reached the target step
                // Use the action's own GameView — lastGameView can be clobbered by GAME_UPDATE.
                if (targetStep != null) {
                    GameView gv = (action.data() instanceof GameClientMessage)
                        ? ((GameClientMessage) action.data()).getGameView() : lastGameView;
                    if (gv != null && gv.getStep() == targetStep) {
                        // Reached the target step — return to LLM
                        Map<String, Object> result = new HashMap<>();
                        result.put("action_pending", true);
                        result.put("action_type", method.name());
    
                        result.put("current_step", gv.getStep().toString());
                        result.put("stop_reason", "reached_step");
                        attachUnseenChat(result);
                        mergeActionChoices(result, boardCursorParam);
                        return result;
                    }
                    // Not at target step: auto-pass (skip playable-cards check)
                    synchronized (actionLock) {
                        if (pendingAction == action) {
                            pendingAction = null;
                        }
                    }
                    session.sendPlayerBoolean(action.gameId(), false);
                    actionsPassed++;
                    continue;
                }

                // Check if there are playable cards (non-mana-only, excluding failed casts)
                // Use the action's own GameView, not lastGameView — a concurrent GAME_UPDATE
                // can overwrite lastGameView with a view from a different phase (forward overwrite).
                GameView viewForPlayableCheck = ((GameClientMessage) action.data()).getGameView();
                PlayableObjectsList playable = viewForPlayableCheck != null ? viewForPlayableCheck.getCanPlayObjects() : null;
                boolean hasPlayableCards = false;
                if (playable != null && !playable.isEmpty()) {
                    for (Map.Entry<UUID, PlayableObjectStats> entry : playable.getObjects().entrySet()) {
                        if (failedManaCasts.contains(entry.getKey())) {
                            continue;
                        }
                        PlayableObjectStats stats = entry.getValue();
                        List<String> abilityNames = stats.getPlayableAbilityNames();
                        List<String> manaNames = stats.getAllManaAbilityNames();
                        boolean allMana = !abilityNames.isEmpty() && manaNames.size() == abilityNames.size();
                        if (!allMana) {
                            hasPlayableCards = true;
                            break;
                        }
                    }
                }

                // Determinism debugging: always log the playable-cards check result
                // to diagnose both Mode 1 (game_seq drift) and Mode 2 (phase divergence).
                {
                    int cbSeq = action.gameSeq();
                    int viewSeq = viewForPlayableCheck != null ? viewForPlayableCheck.getGameSeq() : -1;
                    String viewStep = viewForPlayableCheck != null && viewForPlayableCheck.getStep() != null
                        ? viewForPlayableCheck.getStep().toString() : "null";
                    logger.debug("[" + client.getUsername() + "] passPriority playable check:"
                        + " callback_seq=" + cbSeq
                        + " view_seq=" + viewSeq
                        + " view_step=" + viewStep
                        + " hasPlayable=" + hasPlayableCards
                        + " actionsPassed=" + actionsPassed
                        + " thread=" + Thread.currentThread().getName());
                }

                if (hasPlayableCards) {
                    if (actionsPassed > 0) {
                        // Already passed at least once — return so LLM can decide
                        var result = new HashMap<String, Object>();
                        result.put("action_pending", true);
                        result.put("action_type", method.name());
    
                        result.put("has_playable_cards", true);
                        result.put("stop_reason", "playable_cards");
                        attachUnseenChat(result);
                        mergeActionChoices(result, boardCursorParam);
                        return result;
                    }
                    // First pass — fall through to auto-pass so the game advances
                }

                // No playable cards — auto-pass this priority
                synchronized (actionLock) {
                    if (pendingAction == action) {
                        pendingAction = null;
                    }
                }
                session.sendPlayerBoolean(action.gameId(), false);
                actionsPassed++;

                // Continue waiting for the server to send us the next callback
            }

            synchronized (actionLock) {
                try {
                    actionLock.wait(200);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    break;
                }
            }
            waitLoops++;

            // Periodic progress log: every 30s when the loop is spinning without returning
            {
                long now = System.currentTimeMillis();
                if (now - lastProgressLogAt >= 30_000) {
                    lastProgressLogAt = now;
                    long totalElapsed = now - startTime;
                    logger.warn("[" + client.getUsername() + "] passPriority STILL WAITING:"
                        + " elapsed=" + totalElapsed + "ms"
                        + " waitLoops=" + waitLoops
                        + " actionsPassed=" + actionsPassed
                        + " pendingAction=" + (pendingAction != null)
                        + " playerDead=" + playerDead
                        + " activeGames=" + activeGames.size()
                        + " gameEverStarted=" + gameEverStarted
                        + " lastActionableCallbackAt=" + (lastActionableCallbackAt > 0 ? (now - lastActionableCallbackAt) + "ms ago" : "never")
                        + " lastCallbackReceivedAt=" + (lastCallbackReceivedAt > 0 ? (now - lastCallbackReceivedAt) + "ms ago" : "never")
                        + " currentGameId=" + currentGameId);
                }
            }

            // Game over bail-out: don't block forever if the game ended
            if (playerDead || (activeGames.isEmpty() && gameEverStarted) || !client.isRunning()) {
                long elapsed = System.currentTimeMillis() - startTime;
                long idleSinceCallback = lastActionableCallbackAt > 0
                    ? System.currentTimeMillis() - lastActionableCallbackAt : 0;
                if (idleSinceCallback > 60_000) {
                    // Abnormal: server idle timeout auto-conceded or zombie game
                    logError("passPriority game_over after " + elapsed + "ms idle"
                        + " (lastActionableCallback " + idleSinceCallback + "ms ago)");
                }
                logger.info("[" + client.getUsername() + "] passPriority EXIT game_over:"
                    + " elapsed=" + elapsed + "ms"
                    + " playerDead=" + playerDead
                    + " activeGames=" + activeGames.size()
                    + " actionsPassed=" + actionsPassed);
                var result = new HashMap<String, Object>();
                result.put("action_pending", false);
                result.put("stop_reason", "game_over");
                GameView gvSnap = lastGameView;
                if (gvSnap != null) {
                    result.put("game_seq", gvSnap.getGameSeq());
                }
                attachUnseenChat(result);
                return result;
            }

            // Zombie game detection: no actionable callback for too long means the
            // server game thread is dead. Declare the game over so the pilot exits.
            if (lastActionableCallbackAt > 0) {
                long absoluteIdle = System.currentTimeMillis() - lastActionableCallbackAt;
                if (absoluteIdle > ZOMBIE_GAME_TIMEOUT_MS) {
                    logger.error("[" + client.getUsername() + "] Zombie game detected: "
                            + "no actionable callback for " + absoluteIdle + "ms, declaring game dead");
                    logError("Zombie game detected: no actionable callback for " + absoluteIdle + "ms");
                    playerDead = true;
                }
            }
        }

        // InterruptedException break
        var result = new HashMap<String, Object>();
        result.put("action_pending", false);
        result.put("stop_reason", "interrupted");
        GameView gvSnap = lastGameView;
        if (gvSnap != null) {
            result.put("game_seq", gvSnap.getGameSeq());
        }
        attachUnseenChat(result);
        return result;
    }

    /**
     * Combined helper for models: wait using pass_priority, then return full choices.
     * pass_priority already merges action choices, so this is just a pass-through.
     */
    public Map<String, Object> waitAndGetChoices(String until, Long boardCursorParam) {
        return passPriority(until, boardCursorParam);
    }

    private String getGameLogSince(int offset) {
        synchronized (gameLog) {
            int adjustedOffset = offset - gameLogTrimmedChars;
            if (adjustedOffset >= gameLog.length()) return "";
            // If the caller's reference point was trimmed away, return from the
            // start of the current buffer (oldest surviving entry).
            if (adjustedOffset < 0) adjustedOffset = 0;
            return gameLog.substring(adjustedOffset);
        }
    }

    public Map<String, Object> getGameState(Long cursor) {
        Map<String, Object> fullState = getGameState();
        if (!Boolean.TRUE.equals(fullState.get("available"))) {
            return fullState;
        }
        long currentCursor = updateGameStateCursor(fullState);
        if (cursor != null && cursor.longValue() == currentCursor) {
            var unchanged = new HashMap<String, Object>();
            unchanged.put("available", true);
            unchanged.put("unchanged", true);
            unchanged.put("cursor", currentCursor);
            return unchanged;
        }
        fullState.put("cursor", currentCursor);
        return fullState;
    }

    public Map<String, Object> getGameState() {
        var state = new HashMap<String, Object>();
        GameView gameView = lastGameView;
        if (gameView == null) {
            state.put("available", false);
            state.put("error", "No game state available yet");
            return state;
        }

        state.put("available", true);
        state.put("game_seq", gameView.getGameSeq());
        // Determinism debugging: log what game_seq getGameState returns
        {
            String step = gameView.getStep() != null ? gameView.getStep().toString() : "null";
            logger.debug("[" + client.getUsername() + "] getGameState returning game_seq="
                + gameView.getGameSeq() + " step=" + step
                + " thread=" + Thread.currentThread().getName());
        }
        state.put("turn", roundTracker.update(gameView));

        // Phase info
        if (gameView.getPhase() != null) {
            state.put("phase", gameView.getPhase().toString());
        }
        if (gameView.getStep() != null) {
            state.put("step", gameView.getStep().toString());
        }

        state.put("active_player", gameView.getActivePlayerName());
        state.put("priority_player", gameView.getPriorityPlayerName());

        // Players
        state.put("players", buildPlayersArray(gameView));

        // Stack
        var stack = new ArrayList<Map<String, Object>>();
        if (gameView.getStack() != null) {
            for (CardView card : gameView.getStack().values()) {
                var stackItem = new HashMap<String, Object>();
                if (card.getId() != null) {
                    stackItem.put("id", getStableShortId(card.getId(), card));
                }
                stackItem.put("name", safeDisplayName(card));
                stackItem.put("rules", stripHtmlList(card.getRules()));
                if (card.getTargets() != null && !card.getTargets().isEmpty()) {
                    var targets = new ArrayList<Map<String, Object>>();
                    for (UUID targetId : card.getTargets()) {
                        var t = new HashMap<String, Object>();
                        t.put("id", getStableShortId(targetId));
                        t.put("name", describeTarget(targetId, null, lastGameView));
                        targets.add(t);
                    }
                    stackItem.put("targets", targets);
                }
                if (card.getId() != null) {
                    String owner = castOwners.get(card.getId().toString());
                    if (owner != null) {
                        stackItem.put("owner", owner);
                    }
                }
                stack.add(stackItem);
            }
        }
        state.put("stack", stack);

        // Combat
        List<Map<String, Object>> combatGroups = buildCombatGroups(gameView);
        if (combatGroups != null) {
            state.put("combat", combatGroups);
        }

        return state;
    }

    private List<PlayerView> getStablePlayers(GameView gameView, UUID myPlayerId) {
        var players = new ArrayList<PlayerView>(gameView.getPlayers());
        players.sort((a, b) -> {
            boolean aIsYou = myPlayerId != null && a.getPlayerId().equals(myPlayerId);
            boolean bIsYou = myPlayerId != null && b.getPlayerId().equals(myPlayerId);
            int youCmp = Boolean.compare(bIsYou, aIsYou);
            if (youCmp != 0) {
                return youCmp;
            }
            String aName = a.getName() != null ? a.getName() : "";
            String bName = b.getName() != null ? b.getName() : "";
            int nameCmp = String.CASE_INSENSITIVE_ORDER.compare(aName, bName);
            if (nameCmp != 0) {
                return nameCmp;
            }
            return a.getPlayerId().toString().compareTo(b.getPlayerId().toString());
        });
        return players;
    }

    private boolean isAllPlayerTargets(List<TargetChoice> targetChoices) {
        if (targetChoices.isEmpty()) {
            return false;
        }
        for (TargetChoice choice : targetChoices) {
            if (!"player".equals(choice.entry().get("target_type"))) {
                return false;
            }
        }
        return true;
    }

    /**
     * Build the full players array with board state. Includes hand (ours only),
     * battlefield (with rules), graveyard, exile, mana pool, counters, commanders.
     * Shared by getGameState() and getActionChoices().
     */
    private List<Map<String, Object>> buildPlayersArray(GameView gameView) {
        var players = new ArrayList<Map<String, Object>>();
        UUID gameId = currentGameId; // snapshot volatile to prevent TOCTOU race
        UUID myPlayerId = gameId != null ? activeGames.get(gameId) : null;

        for (PlayerView player : getStablePlayers(gameView, myPlayerId)) {
            var playerInfo = new HashMap<String, Object>();
            playerInfo.put("name", player.getName());
            playerInfo.put("life", player.getLife());
            playerInfo.put("library_size", player.getLibraryCount());
            playerInfo.put("hand_size", player.getHandCount());
            playerInfo.put("is_active", player.isActive());

            boolean isMe = player.getPlayerId().equals(myPlayerId);
            playerInfo.put("is_you", isMe);

            // Hand cards (only for our player)
            if (isMe && gameView.getMyHand() != null) {
                var handCards = new ArrayList<Map<String, Object>>();
                PlayableObjectsList playable = gameView.getCanPlayObjects();

                // Sort hand by card name, then by short ID for deterministic ordering
                var sortedHand = new ArrayList<>(gameView.getMyHand().entrySet());
                sortedHand.sort(Comparator.<Map.Entry<UUID, CardView>, String>comparing(e -> safeDisplayName(e.getValue()))
                    .thenComparingInt(e -> getStableShortIdSequence(e.getKey(), e.getValue())));

                for (Map.Entry<UUID, CardView> handEntry : sortedHand) {
                    var cardInfo = buildCardInfoMap(handEntry.getValue());
                    cardInfo.put("id", getStableShortId(handEntry.getKey(), handEntry.getValue()));
                    if (playable != null && playable.containsObject(handEntry.getKey())) {
                        cardInfo.put("playable", true);
                    }
                    handCards.add(cardInfo);
                }
                playerInfo.put("hand", handCards);
            }

            // Battlefield — sort by name, then by short ID for deterministic ordering
            var battlefield = new ArrayList<Map<String, Object>>();
            if (player.getBattlefield() != null) {
                var sortedBattlefield = new ArrayList<>(player.getBattlefield().values());
                sortedBattlefield.sort(Comparator.<PermanentView, String>comparing(p -> safeDisplayName(p))
                    .thenComparingInt(p -> getStableShortIdSequence(p.getId(), p)));
                for (PermanentView perm : sortedBattlefield) {
                    var permInfo = new HashMap<String, Object>();
                    permInfo.put("id", getStableShortId(perm.getId(), perm));
                    permInfo.put("name", safeDisplayName(perm));
                    permInfo.put("tapped", perm.isTapped());

                    // P/T for creatures
                    if (perm.isCreature()) {
                        permInfo.put("power", perm.getPower());
                        permInfo.put("toughness", perm.getToughness());
                    }

                    // Loyalty for planeswalkers
                    if (perm.isPlaneswalker()) {
                        permInfo.put("loyalty", perm.getLoyalty());
                    }

                    // Counters
                    if (perm.getCounters() != null && !perm.getCounters().isEmpty()) {
                        var counters = new HashMap<String, Integer>();
                        for (CounterView counter : perm.getCounters()) {
                            counters.put(counter.getName(), counter.getCount());
                        }
                        permInfo.put("counters", counters);
                    }

                    // Summoning sickness
                    if (perm.isCreature()) {
                        permInfo.put("summoning_sick", perm.hasSummoningSickness());
                    }

                    // State-deviation flags: info the LLM can't infer from card name alone
                    if (perm.isToken()) {
                        permInfo.put("token", true);
                    }

                    // Detect modified permanents: compare current rules vs printed card rules.
                    // PermanentView.getOriginal() is built with game=null (base abilities only).
                    boolean modified = false;
                    CardView orig = perm.getOriginal();
                    if (orig != null) {
                        modified = !Objects.equals(stripHtmlList(perm.getRules()), stripHtmlList(orig.getRules()));
                    }
                    if (modified) {
                        permInfo.put("modified", true);
                    }

                    // Include oracle text (rules) for all permanents
                    List<String> rules = stripHtmlList(perm.getRules());
                    if (rules != null && !rules.isEmpty()) {
                        permInfo.put("rules", rules);
                    }

                    // Original card name when identity has changed (copy, transform, flip, MDFC, meld)
                    String altName = perm.getAlternateName();
                    if (altName != null && !altName.isEmpty()) {
                        permInfo.put("original_card", altName);
                    }
                    if (perm.isCopy()) {
                        permInfo.put("copy", true);
                    }
                    if (perm.isMorphed() || perm.isManifested()) {
                        permInfo.put("face_down", true);
                    }

                    battlefield.add(permInfo);
                }
            }
            if (!battlefield.isEmpty()) {
                playerInfo.put("battlefield", battlefield);
            }

            // Graveyard — sort by name, then by short ID for deterministic ordering
            var graveyard = new ArrayList<Map<String, Object>>();
            if (player.getGraveyard() != null) {
                var sortedGraveyard = new ArrayList<>(player.getGraveyard().entrySet());
                sortedGraveyard.sort(Comparator.<Map.Entry<UUID, CardView>, String>comparing(e -> safeDisplayName(e.getValue()))
                    .thenComparingInt(e -> getStableShortIdSequence(e.getKey(), e.getValue())));
                for (Map.Entry<UUID, CardView> entry : sortedGraveyard) {
                    var cardInfo = new HashMap<String, Object>();
                    cardInfo.put("id", getStableShortId(entry.getKey(), entry.getValue()));
                    cardInfo.put("name", safeDisplayName(entry.getValue()));
                    List<String> gyRules = stripHtmlList(entry.getValue().getRules());
                    if (gyRules != null && !gyRules.isEmpty()) {
                        cardInfo.put("rules", gyRules);
                    }
                    graveyard.add(cardInfo);
                }
            }
            if (!graveyard.isEmpty()) {
                playerInfo.put("graveyard", graveyard);
            }

            // Exile — sort by name, then by short ID for deterministic ordering
            var exileCards = new ArrayList<Map<String, Object>>();
            if (player.getExile() != null) {
                var sortedExile = new ArrayList<>(player.getExile().entrySet());
                sortedExile.sort(Comparator.<Map.Entry<UUID, CardView>, String>comparing(e -> safeDisplayName(e.getValue()))
                    .thenComparingInt(e -> getStableShortIdSequence(e.getKey(), e.getValue())));
                for (Map.Entry<UUID, CardView> entry : sortedExile) {
                    var cardInfo = new HashMap<String, Object>();
                    cardInfo.put("id", getStableShortId(entry.getKey(), entry.getValue()));
                    cardInfo.put("name", safeDisplayName(entry.getValue()));
                    List<String> exileRules = stripHtmlList(entry.getValue().getRules());
                    if (exileRules != null && !exileRules.isEmpty()) {
                        cardInfo.put("rules", exileRules);
                    }
                    exileCards.add(cardInfo);
                }
            }
            if (!exileCards.isEmpty()) {
                playerInfo.put("exile", exileCards);
            }

            // Mana pool
            ManaPoolView pool = player.getManaPool();
            if (pool != null) {
                int total = pool.getRed() + pool.getGreen() + pool.getBlue()
                          + pool.getWhite() + pool.getBlack() + pool.getColorless();
                if (total > 0) {
                    var mana = new HashMap<String, Integer>();
                    if (pool.getRed() > 0) mana.put("R", pool.getRed());
                    if (pool.getGreen() > 0) mana.put("G", pool.getGreen());
                    if (pool.getBlue() > 0) mana.put("U", pool.getBlue());
                    if (pool.getWhite() > 0) mana.put("W", pool.getWhite());
                    if (pool.getBlack() > 0) mana.put("B", pool.getBlack());
                    if (pool.getColorless() > 0) mana.put("C", pool.getColorless());
                    playerInfo.put("mana_pool", mana);
                }
            }

            // Player counters (poison, etc.)
            if (player.getCounters() != null && !player.getCounters().isEmpty()) {
                var counters = new HashMap<String, Integer>();
                for (CounterView counter : player.getCounters()) {
                    counters.put(counter.getName(), counter.getCount());
                }
                playerInfo.put("counters", counters);
            }

            // Commander info
            if (player.getCommandObjectList() != null && !player.getCommandObjectList().isEmpty()) {
                var commanders = new ArrayList<String>();
                for (CommandObjectView cmd : player.getCommandObjectList()) {
                    commanders.add(cmd.getName());
                }
                playerInfo.put("commanders", commanders);
            }

            players.add(playerInfo);
        }
        return players;
    }

    /**
     * Build combat group info from the game view. Returns null if no combat.
     * Shared by getActionChoices() and getGameState().
     */
    private List<Map<String, Object>> buildCombatGroups(GameView gameView) {
        if (gameView == null || gameView.getCombat() == null || gameView.getCombat().isEmpty()) {
            return null;
        }
        var combatGroups = new ArrayList<Map<String, Object>>();
        for (CombatGroupView group : gameView.getCombat()) {
            var groupInfo = new HashMap<String, Object>();
            var attackers = new ArrayList<Map<String, Object>>();
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
                attackers.add(aInfo);
            }
            groupInfo.put("attackers", attackers);
            var blockers = new ArrayList<Map<String, Object>>();
            for (CardView blocker : group.getBlockers().values()) {
                var bInfo = new HashMap<String, Object>();
                if (blocker.getId() != null) {
                    bInfo.put("id", getStableShortId(blocker.getId(), blocker));
                }
                bInfo.put("name", safeDisplayName(blocker));
                if (blocker.getPower() != null) {
                    bInfo.put("power", blocker.getPower());
                    bInfo.put("toughness", blocker.getToughness());
                }
                blockers.add(bInfo);
            }
            if (!blockers.isEmpty()) {
                groupInfo.put("blockers", blockers);
            }
            groupInfo.put("blocked", group.isBlocked());
            groupInfo.put("defending", group.getDefenderName());
            combatGroups.add(groupInfo);
        }
        return combatGroups;
    }

    private long updateGameStateCursor(Map<String, Object> state) {
        String signature = buildStateSignature(state);
        synchronized (stateCursorLock) {
            if (lastGameStateSignature == null || !lastGameStateSignature.equals(signature)) {
                gameStateCursor++;
                lastGameStateSignature = signature;
            }
            return gameStateCursor;
        }
    }

    private long updateBoardCursor(List<Map<String, Object>> players) {
        String signature = buildStateSignature(players);
        synchronized (boardCursorLock) {
            if (lastBoardSignature == null || !lastBoardSignature.equals(signature)) {
                boardCursor++;
                lastBoardSignature = signature;
            }
            return boardCursor;
        }
    }

    private String buildStateSignature(Object value) {
        if (value == null) {
            return "null";
        }
        if (value instanceof Map<?, ?>) {
            var sorted = new TreeMap<String, Object>();
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                sorted.put(String.valueOf(entry.getKey()), entry.getValue());
            }
            var sb = new StringBuilder("{");
            boolean first = true;
            for (Map.Entry<String, Object> entry : sorted.entrySet()) {
                if (!first) sb.append(",");
                sb.append(entry.getKey()).append(":").append(buildStateSignature(entry.getValue()));
                first = false;
            }
            sb.append("}");
            return sb.toString();
        }
        if (value instanceof List<?>) {
            var sb = new StringBuilder("[");
            boolean first = true;
            for (Object item : (List<?>) value) {
                if (!first) sb.append(",");
                sb.append(buildStateSignature(item));
                first = false;
            }
            sb.append("]");
            return sb.toString();
        }
        return String.valueOf(value);
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

    public Map<String, Object> getOracleText(String cardName, String objectId, String[] cardNames, String[] objectIds) {
        var result = new HashMap<String, Object>();

        boolean hasCardName = cardName != null && !cardName.isEmpty();
        boolean hasObjectId = objectId != null && !objectId.isEmpty();
        boolean hasCardNames = cardNames != null && cardNames.length > 0;
        boolean hasObjectIds = objectIds != null && objectIds.length > 0;

        // Validate: exactly one parameter type should be provided
        int providedCount = (hasCardName ? 1 : 0) + (hasObjectId ? 1 : 0) + (hasCardNames ? 1 : 0) + (hasObjectIds ? 1 : 0);
        if (providedCount != 1) {
            result.put("success", false);
            result.put("error", "Provide exactly one of: card_name, object_id, card_names, or object_ids");
            return result;
        }

        // Batch lookup by object IDs
        if (hasObjectIds) {
            var results = new ArrayList<Map<String, Object>>();
            for (String oid : objectIds) {
                var entry = new HashMap<String, Object>();
                if (oid == null) {
                    entry.put("object_id", null);
                    entry.put("error", "null object_id");
                } else {
                    entry.put("object_id", oid);
                    try {
                        UUID uuid = shortIds.resolve(oid);
                        CardView cardView = findCardViewById(uuid);
                        if (cardView != null) {
                            populateCardFields(entry, cardView);
                        } else {
                            entry.put("error", "not found");
                        }
                    } catch (IllegalArgumentException e) {
                        entry.put("error", "unknown short ID: " + oid);
                    }
                }
                results.add(entry);
            }
            result.put("success", true);
            result.put("cards", results);
            return result;
        }

        // Batch lookup by card names
        if (hasCardNames) {
            var results = new ArrayList<Map<String, Object>>();
            for (String name : cardNames) {
                var entry = new HashMap<String, Object>();
                entry.put("name", name);
                CardInfo cardInfo = CardRepository.instance.findCard(name);
                if (cardInfo != null) {
                    populateCardFields(entry, cardInfo);
                } else {
                    entry.put("error", "not found");
                }
                results.add(entry);
            }
            result.put("success", true);
            result.put("cards", results);
            return result;
        }

        // Object ID lookup (in-game, uses short IDs like "p1", "p2")
        if (hasObjectId) {
            try {
                UUID uuid = shortIds.resolve(objectId);
                CardView cardView = findCardViewById(uuid);
                if (cardView != null) {
                    result.put("success", true);
                    populateCardFields(result, cardView);
                    return result;
                } else {
                    result.put("success", false);
                    result.put("error", "Object not found in current game state: " + objectId);
                    return result;
                }
            } catch (IllegalArgumentException e) {
                result.put("success", false);
                result.put("error", "Unknown short ID: " + objectId);
                return result;
            }
        }

        // Card name lookup (database)
        CardInfo cardInfo = CardRepository.instance.findCard(cardName);
        if (cardInfo != null) {
            result.put("success", true);
            populateCardFields(result, cardInfo);
            return result;
        } else {
            result.put("success", false);
            result.put("error", "Card not found in database: " + cardName);
            return result;
        }
    }

    private void populateCardFields(Map<String, Object> entry, CardView cv) {
        entry.put("name", cv.getDisplayName());
        String manaCost = cv.getManaCostStr();
        if (manaCost != null && !manaCost.isEmpty()) {
            entry.put("mana_cost", manaCost);
        }
        String typeText = cv.getTypeText();
        if (typeText != null && !typeText.trim().isEmpty()) {
            entry.put("type", typeText.trim());
        }
        entry.put("rules", stripHtmlList(cv.getRules()));
        if (cv.isCreature() && cv.getPower() != null) {
            entry.put("power", cv.getPower());
            entry.put("toughness", cv.getToughness());
        }
        if (cv.isPlaneswalker()) {
            String loyalty = cv.getStartingLoyalty();
            if (loyalty != null && !loyalty.isEmpty() && !loyalty.equals("0")) {
                entry.put("starting_loyalty", loyalty);
            }
        }
        if (cv.isBattle()) {
            String defense = cv.getStartingDefense();
            if (defense != null && !defense.isEmpty() && !defense.equals("0")) {
                entry.put("starting_defense", defense);
            }
        }
        CardView secondFace = cv.getSecondCardFace();
        if (secondFace != null) {
            var face = new HashMap<String, Object>();
            populateCardFields(face, secondFace);
            entry.put("second_face", face);
        }
    }

    private void populateCardFields(Map<String, Object> entry, CardInfo ci) {
        entry.put("name", ci.getName());
        List<String> manaCosts = ci.getManaCosts(CardInfo.ManaCostSide.ALL);
        if (manaCosts != null && !manaCosts.isEmpty()) {
            entry.put("mana_cost", String.join("", manaCosts));
        }
        String typeText = buildTypeLine(ci);
        if (!typeText.isEmpty()) {
            entry.put("type", typeText);
        }
        entry.put("rules", stripHtmlList(ci.getRules()));
        if (ci.getTypes().contains(CardType.CREATURE) && ci.getPower() != null) {
            entry.put("power", ci.getPower());
            entry.put("toughness", ci.getToughness());
        }
        if (ci.getTypes().contains(CardType.PLANESWALKER)) {
            String loyalty = ci.getStartingLoyalty();
            if (loyalty != null && !loyalty.isEmpty() && !loyalty.equals("0")) {
                entry.put("starting_loyalty", loyalty);
            }
        }
        if (ci.getTypes().contains(CardType.BATTLE)) {
            String defense = ci.getStartingDefense();
            if (defense != null && !defense.isEmpty() && !defense.equals("0")) {
                entry.put("starting_defense", defense);
            }
        }
        // Check for second face (transform, MDFC, flip, adventure)
        String secondName = ci.getSecondSideName();
        if (secondName == null || secondName.isEmpty()) {
            secondName = ci.getDoubleFacedSecondSideName();
        }
        if (secondName == null || secondName.isEmpty()) {
            secondName = ci.getFlipCardName();
        }
        if (secondName == null || secondName.isEmpty()) {
            secondName = ci.getSpellOptionCardName();
        }
        if (secondName != null && !secondName.isEmpty()) {
            CardInfo secondCard = CardRepository.instance.findCard(secondName);
            if (secondCard != null) {
                var face = new HashMap<String, Object>();
                // Don't recurse further — second faces don't have second faces
                face.put("name", secondCard.getName());
                List<String> secondManaCosts = secondCard.getManaCosts(CardInfo.ManaCostSide.ALL);
                if (secondManaCosts != null && !secondManaCosts.isEmpty()) {
                    face.put("mana_cost", String.join("", secondManaCosts));
                }
                String secondType = buildTypeLine(secondCard);
                if (!secondType.isEmpty()) {
                    face.put("type", secondType);
                }
                face.put("rules", stripHtmlList(secondCard.getRules()));
                if (secondCard.getTypes().contains(CardType.CREATURE) && secondCard.getPower() != null) {
                    face.put("power", secondCard.getPower());
                    face.put("toughness", secondCard.getToughness());
                }
                if (secondCard.getTypes().contains(CardType.PLANESWALKER)) {
                    String loyalty = secondCard.getStartingLoyalty();
                    if (loyalty != null && !loyalty.isEmpty() && !loyalty.equals("0")) {
                        face.put("starting_loyalty", loyalty);
                    }
                }
                if (secondCard.getTypes().contains(CardType.BATTLE)) {
                    String defense = secondCard.getStartingDefense();
                    if (defense != null && !defense.isEmpty() && !defense.equals("0")) {
                        face.put("starting_defense", defense);
                    }
                }
                entry.put("second_face", face);
            }
        }
    }

    private static String buildTypeLine(CardInfo ci) {
        StringBuilder sb = new StringBuilder();
        if (!ci.getSupertypes().isEmpty()) {
            sb.append(ci.getSupertypes().stream().map(SuperType::toString).collect(java.util.stream.Collectors.joining(" ")));
            sb.append(" ");
        }
        if (!ci.getTypes().isEmpty()) {
            sb.append(ci.getTypes().stream().map(CardType::toString).collect(java.util.stream.Collectors.joining(" ")));
        }
        if (!ci.getSubTypes().isEmpty()) {
            sb.append(" — ");
            sb.append(ci.getSubTypes().stream().map(SubType::toString).collect(java.util.stream.Collectors.joining(" ")));
        }
        return sb.toString().trim();
    }

    private CardView findCardViewById(UUID objectId) {
        return findCardViewById(objectId, lastGameView);
    }

    private String getStableShortId(UUID objectId) {
        return getStableShortId(objectId, findCardViewById(objectId));
    }

    private String getStableShortId(UUID objectId, CardView cardView) {
        Objects.requireNonNull(objectId, "objectId");
        if (cardView != null) {
            String serverShortId = cardView.getShortId();
            if (serverShortId != null && !serverShortId.isBlank()) {
                // Detect server ID collision before register() overwrites the mapping
                UUID existing = shortIds.tryResolve(serverShortId);
                if (existing != null && !existing.equals(objectId)) {
                    logError("Server short ID collision: " + serverShortId
                        + " was mapped to " + existing + " but server now says " + objectId);
                }
                shortIds.register(objectId, serverShortId);
                return serverShortId;
            }
        }
        return shortIds.getOrAssign(objectId);
    }

    private int getStableShortIdSequence(UUID objectId) {
        return getStableShortIdSequence(objectId, findCardViewById(objectId));
    }

    private int getStableShortIdSequence(UUID objectId, CardView cardView) {
        return parseShortIdSequence(getStableShortId(objectId, cardView));
    }

    private static int parseShortIdSequence(String shortId) {
        if (shortId == null || shortId.length() < 2 || (shortId.charAt(0) != 'p' && shortId.charAt(0) != 'l')) {
            return Integer.MAX_VALUE;
        }
        try {
            return Integer.parseInt(shortId.substring(1));
        } catch (NumberFormatException e) {
            return Integer.MAX_VALUE;
        }
    }

    private CardView findCardViewById(UUID objectId, GameView gameView) {
        if (gameView == null) return null;

        // Check player's hand
        CardView found = gameView.getMyHand().get(objectId);
        if (found != null) {
            return found;
        }

        // Check stack
        found = gameView.getStack().get(objectId);
        if (found != null) {
            return found;
        }

        // Check all players' zones
        for (PlayerView player : gameView.getPlayers()) {
            // Check battlefield
            PermanentView permanent = player.getBattlefield().get(objectId);
            if (permanent != null) {
                return permanent;
            }

            // Check graveyard
            found = player.getGraveyard().get(objectId);
            if (found != null) {
                return found;
            }

            // Check exile
            found = player.getExile().get(objectId);
            if (found != null) {
                return found;
            }
        }

        // Check exile zones
        for (ExileView exileZone : gameView.getExile()) {
            for (CardView card : exileZone.values()) {
                if (card.getId().equals(objectId)) {
                    return card;
                }
            }
        }

        // Check secondary faces of MDFCs in hand — the back face has its own
        // UUID in the playable list but isn't keyed directly in the hand map.
        for (CardView card : gameView.getMyHand().values()) {
            CardView secondFace = card.getSecondCardFace();
            if (secondFace != null && secondFace.getId().equals(objectId)) {
                return secondFace;
            }
        }

        return null;
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
        if (data instanceof GameClientMessage) {
            Map<String, Serializable> options = ((GameClientMessage) data).getOptions();
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
        if (gameView == null) return null;
        for (PlayerView player : gameView.getPlayers()) {
            PermanentView perm = player.getBattlefield().get(objectId);
            if (perm != null) return perm;
        }
        return null;
    }

    public void handleCallback(ClientCallback callback) {
        try {
            callback.decompressData();
            UUID objectId = callback.getObjectId();
            ClientCallbackMethod method = callback.getMethod();
            lastCallbackReceivedAt = System.currentTimeMillis();
            lastCallbackGameId = objectId;
            if (ACTIONABLE_CALLBACKS.contains(method)) {
                lastActionableCallbackAt = System.currentTimeMillis();
            }
            logger.debug("[" + client.getUsername() + "] Callback received: " + method);

            // Bridge JSONL dump: log every callback
            if (bridgeLogPath != null) {
                String summary = null;
                if (method == ClientCallbackMethod.GAME_UPDATE || method == ClientCallbackMethod.GAME_UPDATE_AND_INFORM) {
                    summary = buildBridgeStateSummary();
                } else if (method == ClientCallbackMethod.CHATMESSAGE) {
                    Object chatData = callback.getData();
                    if (chatData instanceof ChatMessage) {
                        ChatMessage chatMsg = (ChatMessage) chatData;
                        summary = chatMsg.getMessageType() + ": " + chatMsg.getMessage();
                    }
                } else if (method == ClientCallbackMethod.GAME_OVER) {
                    summary = "Game over";
                }
                logBridgeEvent(method, summary);
            }

            switch (method) {
                case START_GAME:
                    handleStartGame(objectId, callback);
                    break;

                case GAME_INIT:
                    handleGameInit(objectId, callback);
                    break;

                case GAME_UPDATE:
                case GAME_UPDATE_AND_INFORM:
                    logGameState(objectId, callback);
                    break;

                case GAME_ASK:
                    if (mcpMode) {
                        storePendingAction(objectId, method, callback);
                    } else {
                        handleGameAsk(objectId, callback);
                    }
                    break;

                case GAME_SELECT:
                    if (mcpMode) {
                        storePendingAction(objectId, method, callback);
                    } else {
                        handleGameSelect(objectId, callback);
                    }
                    break;

                case GAME_TARGET:
                    if (mcpMode) {
                        // Auto-select when required and only one legal target
                        GameClientMessage targetCallbackMsg = (GameClientMessage) callback.getData();
                        if (targetCallbackMsg.isFlag()) { // required
                            Set<UUID> autoTargets = findValidTargets(targetCallbackMsg);
                            if (autoTargets != null && autoTargets.size() == 1) {
                                UUID onlyTarget = autoTargets.iterator().next();
                                logger.info("[" + client.getUsername() + "] Auto-selecting single mandatory target: " + onlyTarget.toString().substring(0, 8));
                                // Update game view if available
                                GameView gv = targetCallbackMsg.getGameView();
                                updateLastGameView(gv, "auto_target");
                                session.sendPlayerUUID(objectId, onlyTarget);
                                break;
                            }
                        }
                        storePendingAction(objectId, method, callback);
                    } else {
                        handleGameTarget(objectId, callback);
                    }
                    break;

                case GAME_CHOOSE_ABILITY: {
                    AbilityPickerView picker = (AbilityPickerView) callback.getData();
                    Map<UUID, String> choices = picker.getChoices();
                    GameView gv = picker.getGameView();
                    updateLastGameView(gv, "GAME_CHOOSE_ABILITY");

                    if (mcpMode && choices != null && !choices.isEmpty()) {
                        if (manaPlan != null) {
                            // Mana plan mode: use ability index if specified, otherwise pick first.
                            Integer abilityIdx = manaPlanAbilityIndex;
                            manaPlanAbilityIndex = null;  // consume
                            UUID selected;
                            if (abilityIdx != null) {
                                List<UUID> abilityIds = new ArrayList<>(choices.keySet());
                                if (abilityIdx >= 0 && abilityIdx < abilityIds.size()) {
                                    selected = abilityIds.get(abilityIdx);
                                    logger.info("[" + client.getUsername() + "] Mana plan: selecting ability " + abilityIdx + ": \""
                                            + picker.getMessage() + "\" -> " + choices.get(selected));
                                } else {
                                    logger.warn("[" + client.getUsername() + "] Mana plan: ability index " + abilityIdx
                                            + " out of range (0-" + (abilityIds.size() - 1) + ") for \""
                                            + picker.getMessage() + "\", cancelling spell");
                                    cancelSpellFromBadManaPlan(objectId, null, picker.getMessage());
                                    break;
                                }
                            } else {
                                selected = choices.keySet().iterator().next();
                                if (choices.size() == 1) {
                                    logger.info("[" + client.getUsername() + "] Mana plan: auto-selecting sole ability: \""
                                            + picker.getMessage() + "\" -> " + choices.get(selected));
                                } else {
                                    logger.info("[" + client.getUsername() + "] Mana plan: no ability index, picking first of "
                                            + choices.size() + ": \"" + picker.getMessage() + "\" -> " + choices.get(selected));
                                }
                            }
                            session.sendPlayerUUID(objectId, selected);
                        } else {
                            // No mana plan: let the LLM choose the ability
                            storePendingAction(objectId, method, callback);
                        }
                    } else if (mcpMode) {
                        logger.warn("[" + client.getUsername() + "] Auto-selecting ability: no choices, sending null");
                        session.sendPlayerUUID(objectId, null);
                    } else {
                        handleGameChooseAbility(objectId, callback);
                    }
                    break;
                }

                case GAME_CHOOSE_CHOICE:
                    if (mcpMode) {
                        storePendingAction(objectId, method, callback);
                    } else {
                        handleGameChooseChoice(objectId, callback);
                    }
                    break;

                case GAME_CHOOSE_PILE:
                    if (mcpMode) {
                        storePendingAction(objectId, method, callback);
                    } else {
                        handleGameChoosePile(objectId, callback);
                    }
                    break;

                case GAME_PLAY_MANA:
                case GAME_PLAY_XMANA:
                    // Try auto-tap first; if it fails, let the LLM choose
                    if (!handleGamePlayManaAuto(objectId, callback)) {
                        if (mcpMode) {
                            storePendingAction(objectId, method, callback);
                        } else {
                            // Non-MCP mode: cancel the payment
                            session.sendPlayerBoolean(objectId, false);
                        }
                    }
                    break;

                case GAME_GET_AMOUNT:
                    if (mcpMode) {
                        storePendingAction(objectId, method, callback);
                    } else {
                        handleGameGetAmount(objectId, callback);
                    }
                    break;

                case GAME_GET_MULTI_AMOUNT:
                    if (mcpMode) {
                        storePendingAction(objectId, method, callback);
                    } else {
                        handleGameGetMultiAmount(objectId, callback);
                    }
                    break;

                case GAME_OVER:
                    handleGameOver(objectId, callback);
                    break;

                case END_GAME_INFO:
                    logger.info("[" + client.getUsername() + "] End game info received");
                    break;

                case CHATMESSAGE:
                    handleChatMessage(callback);
                    break;

                case SERVER_MESSAGE:
                case GAME_ERROR:
                case GAME_INFORM_PERSONAL:
                case JOINED_TABLE:
                    logEvent(callback);
                    break;

                case USER_REQUEST_DIALOG:
                    handleUserRequestDialog(callback);
                    break;

                default:
                    logger.debug("[" + client.getUsername() + "] Unhandled callback: " + method);
            }
        } catch (Exception e) {
            logger.error("[" + client.getUsername() + "] Error handling callback: " + callback.getMethod(), e);
            logError("Error handling callback " + callback.getMethod() + ": " + e.getMessage());
        }
    }

    private void storePendingAction(UUID gameId, ClientCallbackMethod method, ClientCallback callback) {
        Object data = callback.getData();
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
        synchronized (actionLock) {
            pendingAction = new PendingAction(gameId, method, data, message, gameSeq);
            actionLock.notifyAll();
        }
        logger.debug("[" + client.getUsername() + "] Stored pending action: " + method + " - " + message);
    }

    private static GameView extractGameView(Object data) {
        if (data instanceof GameClientMessage) {
            return ((GameClientMessage) data).getGameView();
        }
        if (data instanceof AbilityPickerView) {
            return ((AbilityPickerView) data).getGameView();
        }
        return null;
    }

    private String extractMessage(Object data) {
        if (data instanceof GameClientMessage) {
            GameClientMessage msg = (GameClientMessage) data;
            if (msg.getMessage() != null) {
                return msg.getMessage();
            }
            if (msg.getChoice() != null && msg.getChoice().getMessage() != null) {
                return msg.getChoice().getMessage();
            }
        } else if (data instanceof AbilityPickerView) {
            AbilityPickerView picker = (AbilityPickerView) data;
            return picker.getMessage();
        }
        return "";
    }

    /**
     * Clean a string for LLM consumption: strip HTML tags and 3-char hex ID suffixes.
     * Must be applied after internal HTML parsing (cast owner tracking, mana payment extraction).
     */
    private static String stripHtml(String s) {
        if (s == null || s.isEmpty()) return s;
        // Replace <br> tags with ": " before stripping other tags.
        // XMage uses <br> to separate label from card name (e.g. "Choose spell or ability to play<br>Hallowed Fountain").
        // Without this, the tag is stripped and the words run together.
        s = s.replaceAll("(?i)<br\\s*/?>", ": ");
        s = HTML_TAG_PATTERN.matcher(s).replaceAll("");
        s = HEX_SUFFIX_PATTERN.matcher(s).replaceAll("");
        return s;
    }

    /** Strip HTML tags and hex suffixes from each string in a list (e.g. card rules). */
    private static List<String> stripHtmlList(List<String> list) {
        if (list == null) return null;
        var result = new ArrayList<String>(list.size());
        for (String s : list) {
            result.add(stripHtml(s));
        }
        return result;
    }

    private void handleChatMessage(ClientCallback callback) {
        Object data = callback.getData();
        if (data instanceof ChatMessage) {
            ChatMessage chatMsg = (ChatMessage) data;
            String logEntry = null;
            if (chatMsg.getMessageType() == ChatMessage.MessageType.GAME) {
                logEntry = chatMsg.getMessage();
                // Track cast owners: extract player name and object_id from cast messages
                if (logEntry != null && logEntry.contains(" casts ")) {
                    Matcher castMatcher = CAST_OWNER_PATTERN.matcher(logEntry);
                    if (castMatcher.find()) {
                        castOwners.put(castMatcher.group(2), castMatcher.group(1));
                    }
                }
                // Detect when our player has lost the game
                if (!playerDead && logEntry != null && logEntry.contains("has lost the game")
                        && logEntry.contains(client.getUsername())) {
                    playerDead = true;
                    logger.info("[" + client.getUsername() + "] Player death detected from game log");
                }
            } else if (chatMsg.getMessageType() == ChatMessage.MessageType.TALK) {
                // Include player chat so LLM pilots can see each other's messages
                String user = chatMsg.getUsername();
                String msg = chatMsg.getMessage();
                if (user != null && msg != null && !msg.isEmpty()) {
                    logEntry = "[Chat] " + user + ": " + msg;
                    // Buffer chat from other players so pass_priority can surface it
                    if (!user.equals(client.getUsername())) {
                        synchronized (unseenChat) {
                            unseenChat.add(user + ": " + msg);
                        }
                    }
                }
            }
            if (logEntry != null && !logEntry.isEmpty()) {
                // Rewrite "TURN X for <Player> (lives)" to per-player turn numbers: "Player turn N (lives)"
                Matcher turnMatcher = TURN_MSG_PATTERN.matcher(logEntry);
                if (turnMatcher.find()) {
                    String activePlayer = lastGameView != null ? lastGameView.getActivePlayerName() : null;
                    if (activePlayer != null) {
                        int playerTurn = playerTurnCounts.merge(activePlayer, 1, Integer::sum);
                        String rest = logEntry.substring(turnMatcher.end());
                        int parenIdx = rest.indexOf('(');
                        String lifePart = parenIdx >= 0 ? " " + rest.substring(parenIdx).trim() : "";
                        logEntry = activePlayer + " turn " + playerTurn + lifePart;
                    } else {
                        logEntry = "TURN " + roundTracker.getGameRound() + logEntry.substring(turnMatcher.end());
                    }
                }
                synchronized (gameLog) {
                    if (gameLog.length() > 0) {
                        gameLog.append("\n");
                    }
                    gameLog.append(logEntry);
                    // Cap buffer size to prevent unbounded heap growth in long games
                    if (gameLog.length() > MAX_GAME_LOG_CHARS) {
                        int excess = gameLog.length() - MAX_GAME_LOG_CHARS;
                        // Trim from front at a newline boundary to avoid cutting mid-line
                        int trimTo = gameLog.indexOf("\n", excess);
                        if (trimTo > 0) {
                            trimTo++; // include the newline itself
                        } else {
                            trimTo = excess;
                        }
                        gameLog.delete(0, trimTo);
                        gameLogTrimmedChars += trimTo;
                    }
                }
                synchronized (actionLock) {
                    actionLock.notifyAll();
                }
            }
            logger.debug("[" + client.getUsername() + "] Chat: " + chatMsg.getMessage());
        } else {
            logEvent(callback);
        }
    }

    private void handleStartGame(UUID gameId, ClientCallback callback) {
        TableClientMessage message = (TableClientMessage) callback.getData();
        UUID playerId = message.getPlayerId();
        activeGames.put(gameId, playerId);
        currentGameId = gameId;
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

    private void handleGameInit(UUID gameId, ClientCallback callback) {
        GameView gameView = (GameView) callback.getData();
        updateLastGameView(gameView, "GAME_INIT");
        logger.info("[" + client.getUsername() + "] Game initialized: " + gameView.getPlayers().size() + " players");
    }

    private void logGameState(UUID gameId, ClientCallback callback) {
        Object data = callback.getData();
        if (data instanceof GameView) {
            GameView gameView = (GameView) data;
            updateLastGameView(gameView, "GAME_UPDATE");
            logger.debug("[" + client.getUsername() + "] Game update: turn " + gameView.getTurn() +
                    ", phase " + gameView.getPhase() + ", active player " + gameView.getActivePlayerName());
        } else if (data instanceof GameClientMessage) {
            GameClientMessage message = (GameClientMessage) data;
            GameView gameView = message.getGameView();
            if (gameView != null) {
                updateLastGameView(gameView, "GAME_UPDATE_AND_INFORM");
                logger.debug("[" + client.getUsername() + "] Game inform: " + message.getMessage());
            }
        }
    }

    private void handleGameAsk(UUID gameId, ClientCallback callback) {
        GameClientMessage message = (GameClientMessage) callback.getData();
        logger.info("[" + client.getUsername() + "] Ask: \"" + message.getMessage() + "\" -> NO");
        sleepBeforeAction();
        session.sendPlayerBoolean(gameId, false);
    }

    private void handleGameSelect(UUID gameId, ClientCallback callback) {
        GameClientMessage message = (GameClientMessage) callback.getData();
        logger.info("[" + client.getUsername() + "] Select: \"" + message.getMessage() + "\" -> PASS");
        sleepBeforeAction();
        session.sendPlayerBoolean(gameId, false);
    }

    private void handleGameTarget(UUID gameId, ClientCallback callback) {
        GameClientMessage message = (GameClientMessage) callback.getData();
        boolean required = message.isFlag();

        // Try to find valid targets from multiple sources
        Set<UUID> targets = findValidTargets(message);

        sleepBeforeAction();
        if (required && targets != null && !targets.isEmpty()) {
            UUID firstTarget = selectDeterministicTarget(targets, null);
            logger.info("[" + client.getUsername() + "] Target (required): \"" + message.getMessage() + "\" -> " + firstTarget);
            session.sendPlayerUUID(gameId, firstTarget);
        } else {
            logger.info("[" + client.getUsername() + "] Target (optional): \"" + message.getMessage() + "\" -> CANCEL");
            session.sendPlayerBoolean(gameId, false);
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
            if (possibleTargets instanceof Set) {
                Set<UUID> possible = (Set<UUID>) possibleTargets;
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
                if (choice instanceof UUID) {
                    UUID candidate = (UUID) choice;
                    if (targets.contains(candidate)) {
                        return candidate;
                    }
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

    private void handleGameChooseAbility(UUID gameId, ClientCallback callback) {
        AbilityPickerView picker = (AbilityPickerView) callback.getData();
        Map<UUID, String> choices = picker.getChoices();

        sleepBeforeAction();
        if (choices != null && !choices.isEmpty()) {
            UUID firstChoice = choices.keySet().iterator().next();
            String choiceText = choices.get(firstChoice);
            logger.info("[" + client.getUsername() + "] Ability: \"" + picker.getMessage() + "\" -> " + choiceText);
            session.sendPlayerUUID(gameId, firstChoice);
        } else {
            logger.warn("[" + client.getUsername() + "] Ability: no choices available, sending null");
            session.sendPlayerUUID(gameId, null);
        }
    }

    private void handleGameChooseChoice(UUID gameId, ClientCallback callback) {
        GameClientMessage message = (GameClientMessage) callback.getData();
        Choice choice = message.getChoice();

        if (choice == null) {
            logger.warn("[" + client.getUsername() + "] Choice: null choice object");
            session.sendPlayerString(gameId, null);
            return;
        }

        sleepBeforeAction();
        if (choice.isKeyChoice()) {
            Map<String, String> keyChoices = choice.getKeyChoices();
            if (keyChoices != null && !keyChoices.isEmpty()) {
                String firstKey = keyChoices.keySet().iterator().next();
                logger.info("[" + client.getUsername() + "] Choice (key): \"" + choice.getMessage() + "\" -> " + firstKey + " (" + keyChoices.get(firstKey) + ")");
                session.sendPlayerString(gameId, firstKey);
            } else {
                logger.warn("[" + client.getUsername() + "] Choice (key): no choices available");
                session.sendPlayerString(gameId, null);
            }
        } else {
            Set<String> choices = choice.getChoices();
            if (choices != null && !choices.isEmpty()) {
                String firstChoice = choices.iterator().next();
                logger.info("[" + client.getUsername() + "] Choice: \"" + choice.getMessage() + "\" -> " + firstChoice);
                session.sendPlayerString(gameId, firstChoice);
            } else {
                logger.warn("[" + client.getUsername() + "] Choice: no choices available");
                session.sendPlayerString(gameId, null);
            }
        }
    }

    private void handleGameChoosePile(UUID gameId, ClientCallback callback) {
        GameClientMessage message = (GameClientMessage) callback.getData();
        logger.info("[" + client.getUsername() + "] Pile: \"" + message.getMessage() + "\" -> pile 1");
        sleepBeforeAction();
        session.sendPlayerBoolean(gameId, true);
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

    /**
     * Pick the ability that best matches the remaining mana payment cost.
     * Uses lastManaPaymentPrompt to determine which colors are needed,
     * then scores each ability by how many needed colors it produces.
     * Falls back to the first choice if no prompt or no color-specific cost.
     */
    private UUID pickBestAbilityForMana(Map<UUID, String> choices) {
        UUID fallback = choices.keySet().iterator().next();
        String prompt = lastManaPaymentPrompt;
        if (prompt == null) {
            return fallback;
        }

        // Which colors does the payment need?
        Pattern[] colorPatterns = {REGEX_WHITE, REGEX_BLUE, REGEX_BLACK, REGEX_RED, REGEX_GREEN, REGEX_COLORLESS};
        var needed = new ArrayList<Pattern>();
        for (Pattern p : colorPatterns) {
            if (p.matcher(prompt).find()) {
                needed.add(p);
            }
        }
        if (needed.isEmpty()) {
            return fallback; // Generic cost ({1}, {X}) — any mana works
        }

        // Score each ability by how many needed colors it produces
        UUID best = null;
        int bestScore = -1;
        for (Map.Entry<UUID, String> entry : choices.entrySet()) {
            String desc = entry.getValue();
            int score = 0;
            for (Pattern p : needed) {
                if (p.matcher(desc).find()) {
                    score++;
                }
            }
            if (score > bestScore) {
                bestScore = score;
                best = entry.getKey();
            }
        }
        return best != null ? best : fallback;
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
    private boolean cancelSpellFromBadManaPlan(UUID gameId, UUID payingForId, String msg) {
        if (payingForId != null) {
            failedManaCasts.add(payingForId);
        }
        manaPlan = null;
        manaPlanAbilityIndex = null;
        if (mcpMode) {
            synchronized (unseenChat) {
                unseenChat.add("[System] Spell cancelled — mana plan was incorrect or incomplete.");
            }
            logBridgeEvent("SPELL_CANCELLED", "mana plan was incorrect or incomplete");
        }
        session.sendPlayerBoolean(gameId, false);
        return true;
    }

    private UUID getManaPoolPlayerId(UUID gameId, GameView gameView) {
        if (gameView != null) {
            PlayerView myPlayer = gameView.getMyPlayer();
            if (myPlayer != null && myPlayer.getPlayerId() != null) {
                return myPlayer.getPlayerId();
            }
        }
        return activeGames.get(gameId);
    }

    /**
     * Try to auto-tap a mana source. Returns true if a source was tapped,
     * false if no suitable source was found (caller should fall through to LLM).
     */
    private boolean handleGamePlayManaAuto(UUID gameId, ClientCallback callback) {
        return handleGamePlayManaAuto(gameId, (GameClientMessage) callback.getData());
    }

    private boolean handleGamePlayManaAuto(UUID gameId, GameClientMessage message) {
        GameView gameView = message.getGameView();
        updateLastGameView(gameView, "GAME_PLAY_MANA_AUTO");

        String msg = message.getMessage();
        lastManaPaymentPrompt = msg;
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
                    return cancelSpellFromBadManaPlan(gameId, payingForId, msg);
                }
                PlayableObjectsList playableForPlan = gameView != null ? gameView.getCanPlayObjects() : null;
                if (playableForPlan != null) {
                    PlayableObjectStats stats = playableForPlan.getObjects().get(targetId);
                    if (stats != null && !targetId.equals(payingForId) && !failedManaCasts.contains(targetId)) {
                        logger.info("[" + client.getUsername() + "] Mana plan: \"" + msg + "\" -> tapping " + entry.value());
                        poolManaAttempts = 0;
                        session.sendPlayerUUID(gameId, targetId);
                        return true;
                    }
                }
                // ID not found/not available — cancel spell
                logger.warn("[" + client.getUsername() + "] Mana plan: tap target " + entry.value() + " not available, cancelling spell");
                return cancelSpellFromBadManaPlan(gameId, payingForId, msg);
            }

            if ("pool".equals(entry.type())) {
                ManaType manaType = ManaType.valueOf(entry.value());
                UUID manaPlayerId = getManaPoolPlayerId(gameId, gameView);
                if (manaPlayerId != null) {
                    logger.info("[" + client.getUsername() + "] Mana plan: \"" + msg + "\" -> using pool " + manaType);
                    session.sendPlayerManaType(gameId, manaPlayerId, manaType);
                    return true;
                }
                logger.warn("[" + client.getUsername() + "] Mana plan: pool entry failed (no player ID), cancelling spell");
                return cancelSpellFromBadManaPlan(gameId, payingForId, msg);
            }

            // Unknown entry type — cancel spell
            logger.warn("[" + client.getUsername() + "] Mana plan: unknown entry type '" + entry.type() + "', cancelling spell");
            return cancelSpellFromBadManaPlan(gameId, payingForId, msg);
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
                return cancelSpellFromBadManaPlan(gameId, payingForId, msg);
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
                    session.sendPlayerUUID(gameId, objectId);
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
                    if (mcpMode) {
                        synchronized (unseenChat) {
                            unseenChat.add("[System] Spell cancelled — not enough mana to complete payment.");
                        }
                        logBridgeEvent("SPELL_CANCELLED", "not enough mana to complete payment");
                    }
                    session.sendPlayerBoolean(gameId, false);
                    return true;
                }

                if (!canAutoSelectPoolType && mcpMode) {
                    logger.info("[" + client.getUsername() + "] Mana: \"" + msg + "\" -> pool has multiple options, waiting for manual choice");
                    return false;
                }
                ManaType manaType = poolChoices.get(0);
                if (canAutoSelectPoolType) {
                    logger.info("[" + client.getUsername() + "] Mana: \"" + msg + "\" -> using pool " + manaType.toString());
                } else {
                    logger.info("[" + client.getUsername() + "] Mana: \"" + msg + "\" -> using first available pool type " + manaType.toString());
                }
                session.sendPlayerManaType(gameId, manaPlayerId, manaType);
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
        if (mcpMode) {
            synchronized (unseenChat) {
                unseenChat.add("[System] Spell cancelled — not enough mana to complete payment.");
            }
            logBridgeEvent("SPELL_CANCELLED", "not enough mana to complete payment");
        }
        session.sendPlayerBoolean(gameId, false);
        return true;
    }

    private void handleGameGetAmount(UUID gameId, ClientCallback callback) {
        GameClientMessage message = (GameClientMessage) callback.getData();
        int min = message.getMin();
        logger.info("[" + client.getUsername() + "] Amount: \"" + message.getMessage() + "\" (min=" + min + ", max=" + message.getMax() + ") -> " + min);
        sleepBeforeAction();
        session.sendPlayerInteger(gameId, min);
    }

    private void handleGameGetMultiAmount(UUID gameId, ClientCallback callback) {
        GameClientMessage message = (GameClientMessage) callback.getData();
        int count = message.getMessages() != null ? message.getMessages().size() : 0;

        var sb = new StringBuilder();
        if (message.getMessages() != null) {
            for (int i = 0; i < count; i++) {
                if (i > 0) sb.append(" ");
                sb.append(message.getMessages().get(i).defaultValue);
            }
        }

        String result = sb.toString();
        logger.info("[" + client.getUsername() + "] MultiAmount: " + count + " values, defaults -> " + result);
        sleepBeforeAction();
        session.sendPlayerString(gameId, result);
    }

    private void handleGameOver(UUID gameId, ClientCallback callback) {
        GameClientMessage message = (GameClientMessage) callback.getData();

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

        // Pull bridge events one last time BEFORE removing from activeGames.
        // This ensures cachedBridgeEvents is populated before passPriority
        // sees the game as ended (via activeGames.isEmpty()) and returns
        // game_over to Python — preventing a race where get_game_history
        // finds both pullBridgeEvents() empty (game removed) and the cache
        // still empty (not yet populated by handleGameOver).
        UUID playerId = activeGames.get(gameId);
        if (playerId != null) {
            try {
                int savedCursor = bridgeEventCursor;
                bridgeEventCursor = 0; // Pull everything from the start
                List<BridgeLogEntry> events = session.getBridgeEvents(gameId, playerId, bridgeEventCursor);
                if (events != null && !events.isEmpty()) {
                    // Replace cache entirely — we pulled from cursor 0
                    cachedBridgeEvents.clear();
                    cachedBridgeEvents.addAll(events);
                    bridgeEventCursor = events.get(events.size() - 1).index() + 1;
                } else {
                    bridgeEventCursor = savedCursor;
                }
            } catch (Exception e) {
                logger.warn("[" + client.getUsername() + "] Failed to pull final bridge events on game over", e);
            }
        }

        // Remove from activeGames so that concurrent MCP tool calls
        // (pullBridgeEvents, passPriority, concede) see the game as ended.
        // This also triggers passPriority's game-over bail-out
        // (activeGames.isEmpty()), but now the cache is already populated.
        // The remove must still happen before client.stop() to prevent
        // concurrent pullBridgeEvents from calling session.getBridgeEvents()
        // on a dead connection.
        activeGames.remove(gameId);
        synchronized (actionLock) {
            actionLock.notifyAll();
        }
        UUID chatId = gameChatIds.remove(gameId);
        if (chatId != null) {
            session.leaveChat(chatId);
        }
        logger.info("[" + client.getUsername() + "] Game over: " + message.getMessage());

        if (keepAliveAfterGame) {
            // Multi-game session: signal game finished but keep the client alive.
            // The Python side (join_table tool or potato stdin) drives the next game.
            logger.info("[" + client.getUsername() + "] Game ended (keepAlive mode, staying connected)");
            gameFinishedLatch.countDown();
        } else if (mcpMode) {
            // In MCP mode, each game gets its own pilot process + bridge client.
            // Disconnect immediately so the XMage server doesn't auto-join us
            // into the next game in a parallel gauntlet.
            logger.info("[" + client.getUsername() + "] Game ended (MCP mode, stopping client)");
            client.stop();
        } else if (activeGames.isEmpty()) {
            logger.info("[" + client.getUsername() + "] No more active games, stopping client");
            client.stop();
        }
    }

    private void handleUserRequestDialog(ClientCallback callback) {
        UserRequestMessage request = (UserRequestMessage) callback.getData();
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

    private void logEvent(ClientCallback callback) {
        logger.debug("[" + client.getUsername() + "] Event: " + callback.getMethod() + " - " + callback.getData());
    }
}
