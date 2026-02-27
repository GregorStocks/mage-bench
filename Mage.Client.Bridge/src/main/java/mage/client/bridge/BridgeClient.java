package mage.client.bridge;

import mage.cards.decks.DeckCardLists;
import mage.cards.repository.CardScanner;
import mage.constants.TableState;
import mage.players.PlayerType;
import mage.players.net.UserData;
import mage.players.net.UserGroup;
import mage.players.net.UserSkipPrioritySteps;
import mage.remote.Connection;
import mage.remote.MageRemoteException;
import mage.remote.Session;
import mage.remote.SessionImpl;
import mage.view.SeatView;
import mage.view.TableView;
import org.apache.log4j.Logger;

import java.util.Collection;
import java.io.IOException;
import java.util.Locale;
import java.util.UUID;

/**
 * Main entry point for the bridge XMage client.
 *
 * This client connects to an XMage server, joins the first available table
 * with an open human slot, and responds to all game callbacks automatically.
 *
 * Supports three personalities:
 * - potato (default): Auto-responds to all callbacks (passes priority, picks first option)
 * - staller: Same responses as potato, but intentionally delayed and kept alive between games
 * - sleepwalker: Exposes MCP server on stdio for external client control
 *
 * Usage:
 *   java -jar mage-client-bridge.jar --server localhost --port 17171 --username bot1
 *   java -jar mage-client-bridge.jar --personality sleepwalker --server localhost --port 17171
 *
 * Or via system properties:
 *   -Dxmage.bridge.server=localhost
 *   -Dxmage.bridge.port=17171
 *   -Dxmage.bridge.username=bot1
 *   -Dxmage.bridge.password=
 *   -Dxmage.bridge.personality=potato
 */
public class BridgeClient {

    private static final Logger logger = Logger.getLogger(BridgeClient.class);
    private static final int TABLE_POLL_INTERVAL_MS = 1000;
    private static final int TABLE_POLL_TIMEOUT_MS = 60000;
    private static final int PING_INTERVAL_MS = 20000; // 20 seconds, same as normal client
    private static final int DEFAULT_ACTION_DELAY_MS = 500;
    private static final int DEFAULT_STALLER_DELAY_MS = 15000;
    private static final int MAX_RECONNECT_ATTEMPTS = 5;
    private static final int[] RECONNECT_BACKOFF_MS = {2000, 4000, 8000, 16000, 30000};

    private static final String PERSONALITY_POTATO = "potato";
    private static final String PERSONALITY_STALLER = "staller";
    private static final String PERSONALITY_SLEEPWALKER = "sleepwalker";

    public static void main(String[] args) throws Exception {
        String server = getArg(args, "--server", System.getProperty("xmage.bridge.server", "localhost"));
        int port = getIntArg(args, "--port", Integer.getInteger("xmage.bridge.port", 17171));
        String username = getArg(args, "--username", System.getProperty("xmage.bridge.username", "bridge-" + System.currentTimeMillis()));
        String password = getArg(args, "--password", System.getProperty("xmage.bridge.password", ""));
        String personalityArg = getArg(args, "--personality", System.getProperty("xmage.bridge.personality", PERSONALITY_POTATO));
        String personality = personalityArg.toLowerCase(Locale.ROOT);

        boolean isSleepwalker = PERSONALITY_SLEEPWALKER.equalsIgnoreCase(personality);
        boolean isStaller = PERSONALITY_STALLER.equalsIgnoreCase(personality);
        boolean isPotato = PERSONALITY_POTATO.equalsIgnoreCase(personality);
        boolean keepAlive = Boolean.getBoolean("xmage.bridge.keepAlive");

        if (!isSleepwalker && !isStaller && !isPotato) {
            logger.warn("Unknown personality '" + personalityArg + "', falling back to '" + PERSONALITY_POTATO + "'");
            personality = PERSONALITY_POTATO;
            isPotato = true;
        }

        if (isSleepwalker) {
            logger.info("Starting in SLEEPWALKER mode (MCP server on HTTP)");
        }

        // Log class file timestamp to verify build freshness
        try {
            java.net.URL classUrl = BridgeClient.class.getResource("BridgeClient.class");
            if (classUrl != null && "file".equals(classUrl.getProtocol())) {
                long mtime = new java.io.File(classUrl.toURI()).lastModified();
                logger.info("Build: " + new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss").format(new java.util.Date(mtime)));
            }
        } catch (Exception ignored) {}

        logger.info("Starting bridge client: " + username + "@" + server + ":" + port + " [" + personality + "]");

        // Restrict card pool if requested (used by golden tests for faster startup)
        String allowedSets = System.getProperty("xmage.sets.allowed");
        if (allowedSets != null && !allowedSets.isEmpty()) {
            java.util.Set<String> allowed = new java.util.HashSet<>(java.util.Arrays.asList(allowedSets.split(",")));
            mage.cards.Sets.getInstance().retainOnly(allowed);
            logger.info("Restricted card pool to " + allowed.size() + " sets");
        }

        // Initialize card database so get_oracle_text can look up cards by name
        logger.info("Loading card database...");
        java.io.File lockFile = new java.io.File("./db/cards.lock");
        lockFile.getParentFile().mkdirs();
        try (java.io.RandomAccessFile raf = new java.io.RandomAccessFile(lockFile, "rw");
             java.nio.channels.FileLock lock = raf.getChannel().lock()) {
            CardScanner.scan();
        }
        logger.info("Card database loaded.");

        BridgeMageClient client = new BridgeMageClient(username);
        Session session = new SessionImpl(client);
        client.setSession(session);

        // Get callback handler and configure MCP mode
        BridgeCallbackHandler callbackHandler = client.getCallbackHandler();
        if (isSleepwalker) {
            callbackHandler.setMcpMode(true);
        }
        int actionDelayMs = isStaller
                ? Integer.getInteger("xmage.bridge.stallerDelayMs", DEFAULT_STALLER_DELAY_MS)
                : DEFAULT_ACTION_DELAY_MS;
        actionDelayMs = Integer.getInteger("xmage.bridge.actionDelayMs", actionDelayMs);
        callbackHandler.setActionDelayMs(actionDelayMs);
        callbackHandler.setKeepAliveAfterGame(isStaller || keepAlive);
        String errorLogPath = System.getProperty("xmage.bridge.errorlog");
        if (errorLogPath != null && !errorLogPath.isEmpty()) {
            callbackHandler.setErrorLogPath(errorLogPath);
        }
        String bridgeLogPath = System.getProperty("xmage.bridge.bridgelog");
        if (bridgeLogPath != null && !bridgeLogPath.isEmpty()) {
            callbackHandler.setBridgeLogPath(bridgeLogPath);
        }
        Integer maxInteractions = Integer.getInteger("xmage.bridge.maxInteractionsPerTurn");
        if (maxInteractions != null) {
            callbackHandler.setMaxInteractionsPerTurn(maxInteractions);
        }

        Connection connection = new Connection();
        connection.setHost(server);
        connection.setPort(port);
        connection.setUsername(username);
        connection.setPassword(password);
        connection.setProxyType(Connection.ProxyType.NONE);
        connection.setSocketWriteTimeout(30000);   // 30s (default 10s too short for large game states)
        connection.setNumberOfCallRetries(3);      // JBoss default (default 1 fails on transient errors)

        // Set user data with allowRequestShowHandCards=true so spectators can see hands
        UserData userData = new UserData(
                UserGroup.PLAYER,
                0, // avatarId
                true, // allowRequestShowHandCards - important for streaming spectators
                false, // confirmEmptyManaPool — suppress "mana left in pool" GAME_ASK prompts
                new UserSkipPrioritySteps(),
                "world", // flagName
                false, // askMoveToGraveOrder
                true, // manaPoolAutomatic
                true, // manaPoolAutomaticRestricted
                false, // passPriorityCast
                false, // passPriorityActivation
                true, // autoOrderTrigger
                1, // autoTargetLevel
                true, // useSameSettingsForReplacementEffects
                false, // useFirstManaAbility
                "" // userIdStr
        );
        connection.setUserData(userData);

        logger.info("Connecting to server...");
        if (!session.connectStart(connection)) {
            logger.error("Failed to connect: " + session.getLastError());
            System.exit(1);
        }

        logger.info("Connected! Looking for tables to join...");

        // Try to join a table and start the match
        UUID roomId = session.getMainRoomId();
        if (roomId == null) {
            logger.error("Failed to get main room ID");
            session.connectStop(false, false);
            System.exit(1);
        }

        // In keepAlive mode for sleepwalker, skip initial deck load and table join —
        // the join_table MCP tool handles everything. For potato keepAlive, the stdin
        // loop below handles deck loading and table joining.
        if (keepAlive && isSleepwalker) {
            logger.info("keepAlive mode: skipping initial table join (join_table tool will drive game lifecycle)");
        } else if (keepAlive && !isSleepwalker) {
            logger.info("keepAlive mode: skipping initial table join (stdin commands will drive game lifecycle)");
        } else {
            String deckPath = getArg(args, "--deck", System.getProperty("xmage.bridge.deck"));
            DeckCardLists deck = loadDeck(deckPath);
            callbackHandler.setDeckList(deck);

            UUID tableId = tryJoinTable(session, roomId, username, deck);
            if (tableId == null) {
                logger.error("Failed to join any table within timeout");
                session.connectStop(false, false);
                System.exit(1);
            }

            logger.info("Joined table, waiting for game to start (table creator will start match)...");
        }

        if (isSleepwalker) {
            // Set up JoinHandler so the join_table MCP tool can trigger table joining
            if (keepAlive) {
                callbackHandler.setJoinHandler(deckPath -> {
                    DeckCardLists d = loadDeck(deckPath);
                    return tryJoinTable(session, roomId, username, d);
                });
            }

            // Start MCP server on HTTP
            int mcpPort = Integer.getInteger("xmage.bridge.mcpPort", 0);
            if (mcpPort == 0) {
                logger.error("xmage.bridge.mcpPort system property is required for sleepwalker mode");
                System.exit(1);
            }
            logger.info("Starting MCP HTTP server on port " + mcpPort + "...");
            McpServer mcpServer = new McpServer(client, keepAlive);

            // Run MCP server in separate thread so we can monitor client state
            Thread mcpThread = new Thread(() -> mcpServer.start(mcpPort), "MCP-Server");
            mcpThread.setDaemon(true);
            mcpThread.start();

            // In keepAlive mode, stdin is the lifecycle signal — when the Python
            // side closes stdin, we shut down.  In non-keepAlive mode, we watch
            // client.isRunning() (game over) instead.
            //
            // Start a stdin-reader thread that sets stdinClosed when EOF is reached.
            java.util.concurrent.atomic.AtomicBoolean stdinClosed = new java.util.concurrent.atomic.AtomicBoolean(false);
            Thread stdinThread = new Thread(() -> {
                try {
                    // Block until stdin is closed
                    while (System.in.read() != -1) { /* drain */ }
                } catch (IOException ignored) {
                }
                stdinClosed.set(true);
            }, "MCP-Stdin-Watcher");
            stdinThread.setDaemon(true);
            stdinThread.start();

            int reconnectAttempts = 0;
            outer:
            while (true) {
                long lastPingTime = System.currentTimeMillis();
                while (keepAlive ? !stdinClosed.get() : client.isRunning()) {
                    try {
                        Thread.sleep(1000);
                        long now = System.currentTimeMillis();
                        if (now - lastPingTime >= PING_INTERVAL_MS) {
                            session.ping();
                            lastPingTime = now;
                        }
                    } catch (InterruptedException e) {
                        logger.info("Interrupted, stopping...");
                        client.stop();
                        mcpServer.stop();
                        break outer;
                    }
                }

                if (keepAlive) {
                    // Stdin closed — Python side is done, exit cleanly
                    logger.info("Stdin closed (keepAlive mode), shutting down");
                    break;
                }

                // Client stopped — check if we should reconnect
                if (client.isReconnectable() && reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
                    String oldSessionId = session.getSessionId();
                    logger.info("Connection lost — attempting reconnection (session=" + oldSessionId + ")");
                    session.setRestoreSessionId(oldSessionId);
                    client.suppressDisconnectCallbacks(true);

                    boolean reconnected = false;
                    for (int i = reconnectAttempts; i < MAX_RECONNECT_ATTEMPTS; i++) {
                        int backoffMs = RECONNECT_BACKOFF_MS[i];
                        logger.info("Reconnect attempt " + (i + 1) + "/" + MAX_RECONNECT_ATTEMPTS + " in " + backoffMs + "ms...");
                        try {
                            Thread.sleep(backoffMs);
                        } catch (InterruptedException e) {
                            logger.info("Interrupted during reconnect backoff");
                            break;
                        }
                        if (session.connectStart(connection)) {
                            logger.info("Reconnected successfully on attempt " + (i + 1));
                            client.suppressDisconnectCallbacks(false);
                            client.setRunning(true);
                            reconnectAttempts = 0;
                            reconnected = true;
                            continue outer;
                        }
                        logger.warn("Reconnect attempt " + (i + 1) + " failed: " + session.getLastError());
                        reconnectAttempts = i + 1;
                    }

                    client.suppressDisconnectCallbacks(false);
                    if (!reconnected) {
                        logger.error("All " + MAX_RECONNECT_ATTEMPTS + " reconnect attempts failed — giving up");
                        break;
                    }
                } else {
                    // Game ended normally — stop the HTTP server
                    logger.info("Game ended, shutting down MCP server...");
                    break;
                }
            }
            mcpServer.stop();
        } else if (keepAlive) {
            // Potato/staller keepAlive mode: read deck paths from stdin, join tables, play games.
            // Each line on stdin is an absolute path to a deck file. The potato loads it,
            // resets state, joins the next available table, plays the game, then reads again.
            // When stdin closes, exit cleanly.
            logger.info("Entering keepAlive stdin loop (potato mode)...");
            java.io.BufferedReader stdinReader = new java.io.BufferedReader(new java.io.InputStreamReader(System.in));

            // Background thread for pinging the server to stay connected
            Thread pingThread = new Thread(() -> {
                while (!Thread.currentThread().isInterrupted()) {
                    try {
                        Thread.sleep(PING_INTERVAL_MS);
                        session.ping();
                    } catch (InterruptedException e) {
                        break;
                    }
                }
            }, "Potato-Ping");
            pingThread.setDaemon(true);
            pingThread.start();

            try {
                String line;
                while ((line = stdinReader.readLine()) != null) {
                    String deckPathLine = line.trim();
                    if (deckPathLine.isEmpty()) continue;
                    logger.info("keepAlive: received deck path: " + deckPathLine);
                    DeckCardLists deck = loadDeck(deckPathLine);
                    BridgeCallbackHandler fresh = client.getCallbackHandler().createFreshForNextGame();
                    fresh.setDeckList(deck);
                    UUID tableId = tryJoinTable(session, roomId, username, deck);
                    if (tableId == null) {
                        logger.error("keepAlive: failed to join table, continuing to read stdin...");
                        continue;
                    }
                    logger.info("keepAlive: joined table " + tableId + ", waiting for game to finish...");
                    fresh.awaitGameFinished(600_000); // 10 min max per game
                    logger.info("keepAlive: game finished, ready for next");
                }
            } catch (java.io.IOException e) {
                logger.info("keepAlive: stdin read error: " + e.getMessage());
            }

            pingThread.interrupt();
            logger.info("keepAlive: stdin closed, exiting");
        } else {
            // Potato/staller mode: keep alive while client is running, with reconnection support
            int reconnectAttempts = 0;
            outer:
            while (true) {
                long lastPingTime = System.currentTimeMillis();
                while (client.isRunning()) {
                    try {
                        Thread.sleep(1000);
                        long now = System.currentTimeMillis();
                        if (now - lastPingTime >= PING_INTERVAL_MS) {
                            session.ping();
                            lastPingTime = now;
                        }
                    } catch (InterruptedException e) {
                        logger.info("Interrupted, stopping...");
                        client.stop();
                        break outer;
                    }
                }

                // Client stopped — check if we should reconnect
                if (client.isReconnectable() && reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
                    String oldSessionId = session.getSessionId();
                    logger.info("Connection lost — attempting reconnection (session=" + oldSessionId + ")");
                    session.setRestoreSessionId(oldSessionId);
                    client.suppressDisconnectCallbacks(true);

                    boolean reconnected = false;
                    for (int i = reconnectAttempts; i < MAX_RECONNECT_ATTEMPTS; i++) {
                        int backoffMs = RECONNECT_BACKOFF_MS[i];
                        logger.info("Reconnect attempt " + (i + 1) + "/" + MAX_RECONNECT_ATTEMPTS + " in " + backoffMs + "ms...");
                        try {
                            Thread.sleep(backoffMs);
                        } catch (InterruptedException e) {
                            logger.info("Interrupted during reconnect backoff");
                            break;
                        }
                        if (session.connectStart(connection)) {
                            logger.info("Reconnected successfully on attempt " + (i + 1));
                            client.suppressDisconnectCallbacks(false);
                            client.setRunning(true);
                            reconnectAttempts = 0;
                            reconnected = true;
                            continue outer;
                        }
                        logger.warn("Reconnect attempt " + (i + 1) + " failed: " + session.getLastError());
                        reconnectAttempts = i + 1;
                    }

                    client.suppressDisconnectCallbacks(false);
                    if (!reconnected) {
                        logger.error("All " + MAX_RECONNECT_ATTEMPTS + " reconnect attempts failed — giving up");
                        break;
                    }
                } else {
                    break;
                }
            }
        }

        logger.info("Disconnecting...");
        session.connectStop(false, false);
        logger.info("Done.");
    }

    private static UUID tryJoinTable(Session session, UUID roomId, String username, DeckCardLists deck) {
        long startTime = System.currentTimeMillis();

        while (System.currentTimeMillis() - startTime < TABLE_POLL_TIMEOUT_MS) {
            try {
                Collection<TableView> tables = session.getTables(roomId);
                if (tables != null) {
                    for (TableView table : tables) {
                        if (table.getTableState() == TableState.WAITING) {
                            // Check for empty human seats
                            for (SeatView seat : table.getSeats()) {
                                if (seat.getPlayerType() == PlayerType.HUMAN &&
                                    (seat.getPlayerId() == null || seat.getPlayerName().isEmpty())) {
                                    logger.info("Found table with open seat: " + table.getTableId() + " (" + table.getTableName() + ")");
                                    if (session.joinTable(roomId, table.getTableId(), username, PlayerType.HUMAN, 1, deck, "")) {
                                        logger.info("Successfully joined table " + table.getTableId());
                                        return table.getTableId();
                                    } else {
                                        logger.warn("Failed to join table " + table.getTableId() + ", trying another...");
                                    }
                                }
                            }
                        }
                    }
                }
            } catch (MageRemoteException e) {
                logger.warn("Error getting tables: " + e.getMessage());
            }

            try {
                Thread.sleep(TABLE_POLL_INTERVAL_MS);
            } catch (InterruptedException e) {
                break;
            }
        }

        return null;
    }

    private static boolean waitAndStartMatch(Session session, UUID roomId, UUID tableId) {
        long startTime = System.currentTimeMillis();

        while (System.currentTimeMillis() - startTime < TABLE_POLL_TIMEOUT_MS) {
            try {
                Collection<TableView> tables = session.getTables(roomId);
                if (tables != null) {
                    for (TableView table : tables) {
                        if (table.getTableId().equals(tableId)) {
                            TableState state = table.getTableState();
                            if (state == TableState.READY_TO_START) {
                                logger.info("Table is ready, starting match...");
                                if (session.startMatch(roomId, tableId)) {
                                    logger.info("Match started successfully");
                                    return true;
                                } else {
                                    logger.warn("Failed to start match, will retry...");
                                }
                            } else if (state == TableState.STARTING || state == TableState.DUELING) {
                                logger.info("Match already starting/started");
                                return true;
                            } else {
                                logger.debug("Table state: " + state + ", waiting...");
                            }
                            break;
                        }
                    }
                }
            } catch (MageRemoteException e) {
                logger.warn("Error getting tables: " + e.getMessage());
            }

            try {
                Thread.sleep(TABLE_POLL_INTERVAL_MS);
            } catch (InterruptedException e) {
                break;
            }
        }

        return false;
    }

    public static DeckCardLists loadDeck(String deckPath) {
        if (deckPath == null || deckPath.isEmpty()) {
            logger.info("No deck path specified, using test deck");
            return createTestDeck();
        }

        java.io.File deckFile = new java.io.File(deckPath);
        if (!deckFile.exists()) {
            logger.warn("Deck file not found: " + deckPath + ", using test deck");
            return createTestDeck();
        }

        try {
            // Parse deck file directly without needing CardRepository
            // Format: "count [SET:number] Card Name" or "SB: count [SET:number] Card Name"
            DeckCardLists deck = new DeckCardLists();
            java.util.regex.Pattern pattern = java.util.regex.Pattern.compile(
                "^(SB:\\s*)?(\\d+)\\s+\\[([^:]+):(\\d+)\\]\\s+(.+)$"
            );

            try (java.io.BufferedReader reader = new java.io.BufferedReader(new java.io.FileReader(deckFile))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    line = line.trim();
                    if (line.isEmpty() || line.startsWith("#") || line.startsWith("//")) {
                        continue;
                    }

                    java.util.regex.Matcher matcher = pattern.matcher(line);
                    if (matcher.matches()) {
                        boolean isSideboard = matcher.group(1) != null;
                        int count = Integer.parseInt(matcher.group(2));
                        String setCode = matcher.group(3);
                        String cardNumber = matcher.group(4);
                        String cardName = matcher.group(5).trim();

                        mage.cards.decks.DeckCardInfo cardInfo = new mage.cards.decks.DeckCardInfo(
                            cardName, cardNumber, setCode, count
                        );

                        if (isSideboard) {
                            deck.getSideboard().add(cardInfo);
                        } else {
                            deck.getCards().add(cardInfo);
                        }
                    }
                }
            }

            if (deck.getCards().isEmpty() && deck.getSideboard().isEmpty()) {
                logger.warn("Deck is empty after parsing: " + deckPath + ", using test deck");
                return createTestDeck();
            }

            logger.info("Loaded deck from " + deckPath + " with " +
                    deck.getCards().size() + " main deck cards and " +
                    deck.getSideboard().size() + " sideboard cards");
            return deck;
        } catch (Exception e) {
            logger.warn("Failed to load deck from " + deckPath + ", using test deck", e);
            return createTestDeck();
        }
    }

    private static DeckCardLists createTestDeck() {
        // Simple test deck for Freeform Commander (99 cards + 1 commander)
        DeckCardLists deck = new DeckCardLists();
        // Add basic lands - 99 total for the main deck
        deck.getCards().add(new mage.cards.decks.DeckCardInfo("Swamp", "1", "SLD", 20));
        deck.getCards().add(new mage.cards.decks.DeckCardInfo("Forest", "1", "SLD", 20));
        deck.getCards().add(new mage.cards.decks.DeckCardInfo("Island", "1", "SLD", 20));
        deck.getCards().add(new mage.cards.decks.DeckCardInfo("Mountain", "1", "SLD", 20));
        deck.getCards().add(new mage.cards.decks.DeckCardInfo("Plains", "1", "SLD", 19));
        // Add a legendary creature as commander (Child of Alara - 5-color, from Conflux)
        deck.getSideboard().add(new mage.cards.decks.DeckCardInfo("Child of Alara", "72", "CON", 1));
        return deck;
    }

    private static String getArg(String[] args, String name, String defaultValue) {
        for (int i = 0; i < args.length - 1; i++) {
            if (args[i].equals(name)) {
                return args[i + 1];
            }
        }
        return defaultValue;
    }

    private static int getIntArg(String[] args, String name, int defaultValue) {
        String value = getArg(args, name, null);
        if (value != null) {
            try {
                return Integer.parseInt(value);
            } catch (NumberFormatException e) {
                logger.warn("Invalid integer for " + name + ": " + value + ", using default: " + defaultValue);
            }
        }
        return defaultValue;
    }

}
