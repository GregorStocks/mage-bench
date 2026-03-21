package mage.client.bridge;

import mage.cards.decks.DeckCardInfo;
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
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Main entry point for the bridge XMage client.
 *
 * This client connects to an XMage server and exposes the bridge MCP server
 * over HTTP for external control.
 *
 * Higher-level player roles such as pilot, replay, and the Python-side
 * sleepwalker are not Java bridge personalities; they are Python processes
 * layered on top of this bridge JVM.
 *
 * Usage:
 *   java -jar mage-client-bridge.jar --server localhost --port 17171 --username bot1
 *
 * Or via system properties:
 *   -Dxmage.bridge.server=localhost
 *   -Dxmage.bridge.port=17171
 *   -Dxmage.bridge.username=bot1
 *   -Dxmage.bridge.password=
 */
public class BridgeClient {

    private static final Logger logger = Logger.getLogger(BridgeClient.class);
    private static final int TABLE_POLL_INTERVAL_MS = 1000;
    private static final int TABLE_POLL_TIMEOUT_MS = 60000;
    private static final int PING_INTERVAL_MS = 20000; // 20 seconds, same as normal client
    private static final int MAX_RECONNECT_ATTEMPTS = 5;
    private static final int[] RECONNECT_BACKOFF_MS = {2000, 4000, 8000, 16000, 30000};
    private static final String DECK_NAME_PREFIX = "NAME:";
    private static final String DECK_AUTHOR_PREFIX = "AUTHOR:";
    private static final String DECK_LAYOUT_PREFIX = "LAYOUT ";
    private static final Pattern DCK_CARD_LINE_PATTERN = Pattern.compile(
        "^(SB:)?\\s*(\\d+)\\s*\\[([^]:]+):([^]:]+)\\]\\s*(.*)\\s*$"
    );

    public static void main(String[] args) throws Exception {
        String server = getStringSetting(args, "--server", "xmage.bridge.server", "localhost");
        int port = getIntSetting(args, "--port", "xmage.bridge.port", 17171);
        String username = getStringSetting(
            args, "--username", "xmage.bridge.username", "bridge-" + System.currentTimeMillis()
        );
        String password = getStringSetting(args, "--password", "xmage.bridge.password", "");

        boolean keepAlive = Boolean.getBoolean("xmage.bridge.keepAlive");

        logger.info("Starting in SLEEPWALKER mode (MCP server on HTTP)");

        // Log class file timestamp to verify build freshness
        try {
            java.net.URL classUrl = BridgeClient.class.getResource("BridgeClient.class");
            if (classUrl != null && "file".equals(classUrl.getProtocol())) {
                long mtime = new java.io.File(classUrl.toURI()).lastModified();
                logger.info("Build: " + new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss").format(new java.util.Date(mtime)));
            }
        } catch (Exception ignored) {}

        logger.info("Starting bridge client: " + username + "@" + server + ":" + port);

        // Skip bulk card scanning — CardRepository.findCard() lazily loads
        // individual cards on demand from ExpansionSet definitions. This avoids
        // loading all ~30K card classes at startup.
        CardScanner.scanned = true;

        BridgeMageClient client = new BridgeMageClient(username);
        Session session = new SessionImpl(client);
        client.setSession(session);

        BridgeCallbackHandler callbackHandler = client.getCallbackHandler();
        callbackHandler.setKeepAliveAfterGame(keepAlive);
        String errorLogPath = System.getProperty("xmage.bridge.errorlog");
        if (errorLogPath != null && !errorLogPath.isEmpty()) {
            callbackHandler.setErrorLogPath(errorLogPath);
        }
        String bridgeLogPath = System.getProperty("xmage.bridge.bridgelog");
        if (bridgeLogPath != null && !bridgeLogPath.isEmpty()) {
            callbackHandler.setBridgeLogPath(bridgeLogPath);
        }
        Integer maxInteractions = getOptionalIntProperty("xmage.bridge.maxInteractionsPerTurn");
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

        // In keepAlive mode, skip initial deck load and table join — the join_table
        // MCP tool handles the game lifecycle.
        if (keepAlive) {
            logger.info("keepAlive mode: skipping initial table join (join_table tool will drive game lifecycle)");
        } else {
            String deckPath = getStringSetting(args, "--deck", "xmage.bridge.deck", null);
            DeckCardLists deck = loadDeck(deckPath);
            callbackHandler.setDeckList(deck);

            String tableIdProp = System.getProperty("xmage.bridge.tableId");
            UUID targetTableId = tableIdProp != null ? UUID.fromString(tableIdProp) : null;
            UUID tableId = tryJoinTable(session, roomId, username, deck, targetTableId);
            if (tableId == null) {
                logger.error("Failed to join any table within timeout");
                session.connectStop(false, false);
                System.exit(1);
            }

            logger.info("Joined table, waiting for game to start (table creator will start match)...");
        }

        // Set up JoinHandler so the join_table MCP tool can trigger table joining
        if (keepAlive) {
            callbackHandler.setJoinHandler((deckPath, targetTableId) -> {
                DeckCardLists d = loadDeck(deckPath);
                return tryJoinTable(session, roomId, username, d, targetTableId);
            });
        }

        // Start MCP server on HTTP
        int mcpPort = getIntProperty("xmage.bridge.mcpPort", 0);
        if (mcpPort == 0) {
            logger.error("xmage.bridge.mcpPort system property is required for bridge MCP mode");
            System.exit(1);
        }
        logger.info("Starting MCP HTTP server on port " + mcpPort + "...");
        McpServer mcpServer = new McpServer(client, keepAlive);

        // Run MCP server in separate thread so we can monitor client state
        Thread.ofVirtual().name("MCP-Server").start(() -> mcpServer.start(mcpPort));

        // In keepAlive mode, stdin is the lifecycle signal — when the Python
        // side closes stdin, we shut down. In non-keepAlive mode, we watch
        // client.isRunning() (game over) instead.
        java.util.concurrent.atomic.AtomicBoolean stdinClosed = new java.util.concurrent.atomic.AtomicBoolean(false);
        Thread.ofVirtual().name("MCP-Stdin-Watcher").start(() -> {
            try {
                for (int nextByte = System.in.read(); nextByte != -1; nextByte = System.in.read()) {
                    continue;
                }
            } catch (IOException ignored) {
            }
            stdinClosed.set(true);
        });

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
                // Game ended — wait for stdin to close (replay client
                // disconnect) before stopping the MCP server, so
                // in-flight tool calls (get_game_history, concede) can
                // complete cleanly.
                logger.info("Game ended, waiting for replay client to disconnect...");
                while (!stdinClosed.get()) {
                    try {
                        Thread.sleep(100);
                    } catch (InterruptedException e) {
                        break;
                    }
                }
                logger.info("Replay client disconnected, shutting down MCP server...");
                break;
            }
        }
        mcpServer.stop();

        logger.info("Disconnecting...");
        session.connectStop(false, false);
        logger.info("Done.");
    }

    private static UUID tryJoinTable(Session session, UUID roomId, String username, DeckCardLists deck, UUID targetTableId) {
        long startTime = System.currentTimeMillis();

        while (System.currentTimeMillis() - startTime < TABLE_POLL_TIMEOUT_MS) {
            try {
                Collection<TableView> tables = session.getTables(roomId);
                if (tables != null) {
                    for (TableView table : tables) {
                        if (table.getTableState() == TableState.WAITING) {
                            // If a target table is specified, only try that one
                            if (targetTableId != null && !targetTableId.equals(table.getTableId())) {
                                continue;
                            }
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

    public static DeckCardLists loadDeck(String deckPath) {
        if (deckPath == null || deckPath.isBlank()) {
            throw new IllegalArgumentException("Bridge deck path is required");
        }

        java.io.File deckFile = new java.io.File(deckPath);
        if (!deckFile.exists()) {
            throw new IllegalArgumentException("Deck file not found: " + deckPath);
        }
        if (!deckFile.isFile()) {
            throw new IllegalArgumentException("Deck path is not a file: " + deckPath);
        }

        // Parse deck file directly without needing CardRepository.
        // Accept the standard XMage .dck metadata/header lines plus deck card rows.
        DeckCardLists deck = new DeckCardLists();

        try (java.io.BufferedReader reader = new java.io.BufferedReader(new java.io.FileReader(deckFile))) {
            String rawLine;
            int lineNumber = 0;
            while ((rawLine = reader.readLine()) != null) {
                lineNumber++;
                String line = rawLine.trim();
                if (line.isEmpty() || line.startsWith("#") || line.startsWith("//")) {
                    continue;
                }

                if (line.startsWith(DECK_NAME_PREFIX)) {
                    deck.setName(line.substring(DECK_NAME_PREFIX.length()));
                    continue;
                }
                if (line.startsWith(DECK_AUTHOR_PREFIX)) {
                    deck.setAuthor(line.substring(DECK_AUTHOR_PREFIX.length()));
                    continue;
                }
                if (line.startsWith(DECK_LAYOUT_PREFIX)) {
                    continue;
                }

                Matcher matcher = DCK_CARD_LINE_PATTERN.matcher(line);
                if (!matcher.matches()) {
                    throw new IllegalArgumentException(
                        "Invalid deck line " + lineNumber + " in " + deckPath + ": " + line
                    );
                }

                boolean isSideboard = matcher.group(1) != null;
                int count = Integer.parseInt(matcher.group(2));
                String setCode = matcher.group(3);
                String cardNumber = matcher.group(4);
                String cardName = matcher.group(5).trim();

                DeckCardInfo cardInfo = new DeckCardInfo(
                    cardName, cardNumber, setCode, count
                );

                if (isSideboard) {
                    deck.getSideboard().add(cardInfo);
                } else {
                    deck.getCards().add(cardInfo);
                }
            }
        } catch (IOException e) {
            throw new IllegalStateException("Failed to read deck file: " + deckPath, e);
        }

        if (deck.getCards().isEmpty() && deck.getSideboard().isEmpty()) {
            throw new IllegalStateException("Deck is empty after parsing: " + deckPath);
        }

        logger.info("Loaded deck from " + deckPath + " with " +
                deck.getCards().size() + " main deck cards and " +
                deck.getSideboard().size() + " sideboard cards");
        return deck;
    }

    static String getStringSetting(String[] args, String argName, String propertyName, String defaultValue) {
        String argValue = getOptionalArg(args, argName);
        if (argValue != null) {
            return argValue;
        }

        String propertyValue = System.getProperty(propertyName);
        return propertyValue != null ? propertyValue : defaultValue;
    }

    static int getIntSetting(String[] args, String argName, String propertyName, int defaultValue) {
        String argValue = getOptionalArg(args, argName);
        if (argValue != null) {
            return parseIntSetting(argValue, argName);
        }
        return getIntProperty(propertyName, defaultValue);
    }

    static int getIntProperty(String propertyName, int defaultValue) {
        String value = System.getProperty(propertyName);
        return value != null ? parseIntSetting(value, "-D" + propertyName) : defaultValue;
    }

    static Integer getOptionalIntProperty(String propertyName) {
        String value = System.getProperty(propertyName);
        return value != null ? parseIntSetting(value, "-D" + propertyName) : null;
    }

    private static String getOptionalArg(String[] args, String name) {
        for (int i = 0; i < args.length; i++) {
            if (args[i].equals(name)) {
                if (i + 1 >= args.length || args[i + 1].startsWith("--")) {
                    throw new IllegalArgumentException("Missing value for " + name);
                }
                return args[i + 1];
            }
        }
        return null;
    }

    private static int parseIntSetting(String value, String source) {
        try {
            return Integer.parseInt(value);
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("Invalid integer for " + source + ": " + value, e);
        }
    }

}
