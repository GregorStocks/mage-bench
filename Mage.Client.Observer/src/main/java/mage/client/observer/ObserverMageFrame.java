package mage.client.observer;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import mage.MageException;
import mage.cards.decks.DeckCardLists;
import mage.cards.decks.importer.DeckImporter;
import mage.client.MageFrame;
import mage.client.MagePane;
import mage.client.SessionHandler;
import mage.client.game.GamePane;
import mage.client.preference.MagePreferences;
import mage.client.util.AiPuppeteerConfig;
import mage.client.util.IgnoreList;
import mage.constants.*;
import mage.game.match.MatchOptions;
import mage.players.PlayerType;
import mage.remote.Connection;
import mage.util.DeckUtil;
import mage.view.TableView;
import org.apache.log4j.Logger;

import javax.swing.*;
import java.awt.*;
import java.io.BufferedReader;
import java.io.File;
import java.io.InputStreamReader;
import java.lang.reflect.Field;
import java.net.SocketException;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Collection;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

/**
 * observer-optimized MageFrame that uses ObserverGamePane for watching games.
 * Skips the lobby UI and supports auto-watching a table via command-line args.
 */
public class ObserverMageFrame extends MageFrame {

    private static final Logger LOGGER = Logger.getLogger(ObserverMageFrame.class);
    private static final int MAX_RECONNECT_ATTEMPTS = 5;
    private static final int[] RECONNECT_BACKOFF_MS = {2000, 4000, 8000, 16000, 30000};
    private static final boolean NO_WINDOW = Boolean.getBoolean("xmage.observer.noWindow");
    private static final String GIT_BRANCH = getGitBranch();
    private ObserverHealthServer healthServer;
    private String titlePrefix = GIT_BRANCH != null ? "[" + GIT_BRANCH + "] " : "";

    /**
     * Get the current git branch name, or null if not in a git repo.
     */
    private static String getGitBranch() {
        try {
            Process process = new ProcessBuilder("git", "rev-parse", "--abbrev-ref", "HEAD")
                    .redirectErrorStream(true)
                    .start();
            try (BufferedReader reader = new BufferedReader(
                    new InputStreamReader(process.getInputStream()))) {
                String branch = reader.readLine();
                int exitCode = process.waitFor();
                if (exitCode == 0 && branch != null && !branch.isEmpty()) {
                    return branch.trim();
                }
            }
        } catch (Exception e) {
            // Not in a git repo or git not available - that's fine
        }
        return null;
    }

    public ObserverMageFrame() throws MageException {
        super();
        // Hide toolbar after initialization
        SwingUtilities.invokeLater(this::hideToolbar);
    }

    void setHealthServer(ObserverHealthServer healthServer) {
        if (healthServer == null) {
            throw new IllegalArgumentException("Observer health server cannot be null");
        }
        if (this.healthServer != null && this.healthServer != healthServer) {
            throw new IllegalStateException("Observer health server already initialized");
        }
        this.healthServer = healthServer;
    }

    /**
     * Hide the main application toolbar since observer spectators don't need it.
     */
    private void hideToolbar() {
        try {
            Field toolbarField = MageFrame.class.getDeclaredField("mageToolbar");
            toolbarField.setAccessible(true);
            JToolBar toolbar = (JToolBar) toolbarField.get(this);
            if (toolbar != null) {
                toolbar.setVisible(false);
            }
        } catch (NoSuchFieldException | IllegalAccessException e) {
            // Log but don't fail - toolbar visibility is not critical
            System.err.println("Failed to hide toolbar: " + e.getMessage());
        }
    }

    /**
     * Intercept native peer creation so the window is positioned offscreen
     * before the WM ever sees it. MageFrame's constructor calls pack() inside
     * initComponents(), which triggers addNotify() — by that point the WM on
     * tiling/aggressive Linux desktops (i3, sway, KDE, etc.) will map and
     * focus the window. Positioning it here ensures it's offscreen from birth.
     */
    @Override
    public void addNotify() {
        if (NO_WINDOW) {
            setLocation(-10000, -10000);
        }
        super.addNotify();
    }

    /**
     * Prevent the WM maximize hint from stealing focus.
     * MageFrame's constructor calls setExtendedState(MAXIMIZED_BOTH), which
     * on X11/Wayland causes the WM to focus and raise the window. Strip the
     * maximize bits and set explicit screen-sized bounds instead — the window
     * fills the screen without triggering WM focus-steal behavior.
     */
    @Override
    public void setExtendedState(int state) {
        if (NO_WINDOW) {
            // Don't maximize — prevents the WM from mapping the window.
            return;
        }
        if ((state & MAXIMIZED_BOTH) != 0) {
            // Set bounds explicitly instead of requesting WM maximize.
            GraphicsEnvironment ge = GraphicsEnvironment.getLocalGraphicsEnvironment();
            setBounds(ge.getMaximumWindowBounds());
            state &= ~MAXIMIZED_BOTH;
            if (state == 0) {
                return;
            }
        }
        super.setExtendedState(state);
    }

    /**
     * Prevent the observer window from ever stealing OS focus.
     * The parent MageFrame or Swing internals may call toFront() during game
     * events — override it to be a no-op so we never yank focus from the user.
     */
    @Override
    public void toFront() {
        // Intentionally empty — observer should never steal focus
    }

    /**
     * Override setTitle to always add our prefix.
     * This intercepts all title changes from MageFrame.setWindowTitle().
     */
    @Override
    public void setTitle(String title) {
        if (title != null && titlePrefix != null && !title.startsWith(titlePrefix)) {
            super.setTitle(titlePrefix + title);
        } else {
            super.setTitle(title);
        }
    }

    /**
     * Set the game name to display in the window title (e.g. "Player1 vs Player2").
     */
    public void setGameName(String gameName) {
        String oldPrefix = this.titlePrefix;
        String branchPart = GIT_BRANCH != null ? "[" + GIT_BRANCH + "] " : "";
        this.titlePrefix = gameName + " " + branchPart;
        // Strip old prefix from current title and re-apply with new prefix
        String currentTitle = getTitle();
        if (currentTitle != null && currentTitle.startsWith(oldPrefix)) {
            currentTitle = currentTitle.substring(oldPrefix.length());
        }
        super.setTitle(titlePrefix + currentTitle);
    }

    /**
     * Auto-reconnect instead of showing a dialog.
     */
    @Override
    public void disconnected(boolean askToReconnect, boolean keepMySessionActive) {
        LOGGER.info("Disconnected (askToReconnect=" + askToReconnect + ", keepSession=" + keepMySessionActive + ")");

        SessionHandler.disconnect(false, keepMySessionActive);

        if (!askToReconnect) {
            return;
        }

        Thread reconnectThread = new Thread(() -> {
            for (int i = 0; i < MAX_RECONNECT_ATTEMPTS; i++) {
                int backoffMs = RECONNECT_BACKOFF_MS[i];
                LOGGER.info("Reconnect attempt " + (i + 1) + "/" + MAX_RECONNECT_ATTEMPTS + " in " + backoffMs + "ms...");
                try {
                    Thread.sleep(backoffMs);
                } catch (InterruptedException e) {
                    LOGGER.info("Interrupted during reconnect backoff");
                    return;
                }

                Connection connection = buildConnectionFromPreferences();
                if (MageFrame.connect(connection)) {
                    LOGGER.info("Reconnected successfully on attempt " + (i + 1));
                    SwingUtilities.invokeLater(this::prepareAndShowServerLobby);
                    return;
                }
                LOGGER.warn("Reconnect attempt " + (i + 1) + " failed: " + SessionHandler.getLastConnectError());
            }
            LOGGER.error("All " + MAX_RECONNECT_ATTEMPTS + " reconnect attempts failed — giving up");
        }, "ObserverReconnect");
        reconnectThread.setDaemon(true);
        reconnectThread.start();
    }

    private Connection buildConnectionFromPreferences() {
        Connection connection = new Connection();
        connection.setUsername(MagePreferences.getLastServerUser());
        connection.setPassword(MagePreferences.getLastServerPassword());
        connection.setHost(MagePreferences.getLastServerAddress());
        connection.setPort(MagePreferences.getLastServerPort());
        String allMAC = "";
        try {
            allMAC = Connection.getMAC();
        } catch (SocketException ignored) {
        }
        connection.setUserIdStr(System.getProperty("user.name") + ":" + System.getProperty("os.name") + ":" + MagePreferences.getUserNames() + ":" + allMAC);
        connection.setProxyType(Connection.ProxyType.NONE);
        setUserPrefsToConnection(connection);
        return connection;
    }

    /**
     * Suppress popup dialogs — the observer is an unattended recording client.
     * Without this, transient errors (e.g. reconnect failures) show modal Swing
     * dialogs that block the EDT and require manual dismissal.
     */
    @Override
    public void showMessage(String message) {
        LOGGER.warn("Suppressed dialog: " + message);
    }

    @Override
    public void showError(String message) {
        LOGGER.error("Suppressed error dialog: " + message);
    }

    /**
     * Override watchGame to use ObserverGamePane instead of GamePane.
     */
    @Override
    public void watchGame(UUID currentTableId, UUID parentTableId, UUID gameId) {
        // Check if we're already watching this game
        for (Component component : getDesktop().getComponents()) {
            if (component instanceof ObserverGamePane
                    && ((ObserverGamePane) component).getGameId().equals(gameId)) {
                setActive((MagePane) component);
                return;
            }
            // Also check for regular GamePane in case it was created elsewhere
            if (component instanceof GamePane
                    && ((GamePane) component).getGameId().equals(gameId)) {
                setActive((MagePane) component);
                return;
            }
        }

        // Create observer game pane
        ObserverGamePane gamePane = new ObserverGamePane();
        if (healthServer != null) {
            gamePane.setHealthServer(healthServer);
        }
        getDesktop().add(gamePane, JLayeredPane.DEFAULT_LAYER);
        gamePane.setVisible(true);
        gamePane.watchGame(currentTableId, parentTableId, gameId);
        setActive(gamePane);

        // Start recording if configured via system property
        String recordPath = System.getProperty("xmage.observer.record");
        if (recordPath != null && !recordPath.isEmpty()) {
            // Delay recording start to allow the panel to fully render
            SwingUtilities.invokeLater(() -> {
                LOGGER.info("Starting recording to: " + recordPath);
                gamePane.startRecording(Paths.get(recordPath));
            });
        }
    }

    /**
     * Override to initialize lobby (for AI puppeteer game creation) but keep it hidden.
     * The parent method initializes TablesPane which handles auto-start in AI puppeteer mode.
     */
    @Override
    public void prepareAndShowServerLobby() {
        // Call parent to initialize TablesPane (needed for AI puppeteer game creation)
        super.prepareAndShowServerLobby();

        // Then immediately hide the lobby
        LOGGER.info("Observer mode: hiding lobby UI");
        hideServerLobby();

        // In keepAlive mode, signal readiness after lobby init (connection is established)
        if (Boolean.getBoolean("xmage.observer.keepAlive")) {
            LOGGER.info("keepAlive: lobby initialized, ready for commands");
            if (healthServer != null) {
                healthServer.signalLobbyReady();
            }
        }
    }

    /**
     * Set this instance as the MageFrame singleton using reflection.
     * This is necessary because MageFrame.instance is private.
     */
    public static void setInstance(MageFrame frame) {
        try {
            Field instanceField = MageFrame.class.getDeclaredField("instance");
            instanceField.setAccessible(true);
            instanceField.set(null, frame);
        } catch (NoSuchFieldException | IllegalAccessException e) {
            throw new RuntimeException("Failed to set MageFrame instance via reflection", e);
        }
    }

    // -----------------------------------------------------------------------
    // keepAlive mode: stdin-driven game lifecycle for session-scoped spectator
    // -----------------------------------------------------------------------

    /**
     * Start the keepAlive stdin loop. Each line from stdin is a JSON command
     * that creates a new game table. When stdin closes, the JVM exits.
     *
     * JSON command format:
     * {"gameDir":"/path","playersConfig":{"players":[...],"gameType":"...","deckType":"..."},
     *  "choosingPlayer":"TestPlayer","skipInitShuffling":true,"winsNeeded":1}
     */
    public void startKeepAliveLoop() {
        int healthPort = Integer.getInteger("xmage.observer.healthPort", 0);
        if (healthPort > 0 && healthServer == null) {
            throw new IllegalStateException(
                    "Observer health server must be initialized before keepAlive startup on port " + healthPort
            );
        }
        if (healthServer != null) {
            healthServer.signalKeepAliveReady();
        }

        LOGGER.info("keepAlive: ready for commands");

        Thread stdinThread = new Thread(() -> {
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(System.in))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    line = line.trim();
                    if (line.isEmpty()) continue;
                    LOGGER.info("keepAlive: received command: " + line);
                    try {
                        handleKeepAliveCommand(line);
                    } catch (Exception e) {
                        LOGGER.error("keepAlive: command failed", e);
                    }
                }
            } catch (Exception e) {
                LOGGER.info("keepAlive: stdin read error: " + e.getMessage());
            }
            LOGGER.info("keepAlive: stdin closed, exiting");
            System.exit(0);
        }, "Observer-KeepAlive-Stdin");
        stdinThread.setDaemon(true);
        stdinThread.start();
    }

    private void handleKeepAliveCommand(String json) throws Exception {
        Gson gson = new Gson();
        JsonObject cmd = gson.fromJson(json, JsonObject.class);

        String gameDir = cmd.get("gameDir").getAsString();
        JsonObject playersConfigObj = cmd.getAsJsonObject("playersConfig");
        String choosingPlayer = cmd.has("choosingPlayer") ? cmd.get("choosingPlayer").getAsString() : null;
        boolean skipInitShuffling = cmd.has("skipInitShuffling") && cmd.get("skipInitShuffling").getAsBoolean();
        int winsNeeded = cmd.has("winsNeeded") ? cmd.get("winsNeeded").getAsInt() : 1;

        // Update game directory for the new game
        System.setProperty("xmage.observer.gameDir", gameDir);

        // Clean up any previous game pane
        SwingUtilities.invokeAndWait(this::cleanUpCurrentGame);

        // Parse player config
        AiPuppeteerConfig config = gson.fromJson(playersConfigObj.toString(), AiPuppeteerConfig.class);

        // Create the game table
        UUID roomId = SessionHandler.getSession().getMainRoomId();
        assert roomId != null : "keepAlive: no main room ID";

        UUID tableId = createGameTable(roomId, config, gameDir, choosingPlayer, skipInitShuffling, winsNeeded);

        // Start watching for the game to begin
        watchForGameStart(roomId, tableId);
    }

    /**
     * Remove any existing ObserverGamePane from the desktop.
     * Must be called on the EDT.
     */
    private void cleanUpCurrentGame() {
        for (Component component : getDesktop().getComponents()) {
            if (component instanceof ObserverGamePane) {
                ((ObserverGamePane) component).removeGame();
                LOGGER.info("keepAlive: cleaned up previous game pane");
            }
        }
    }

    /**
     * Create a game table directly via SessionHandler.
     * Replicates the essential logic from TablesPanel.createConfiguredAiPuppeteerGame()
     * but uses explicit parameters instead of environment variables.
     */
    private UUID createGameTable(
            UUID roomId,
            AiPuppeteerConfig config,
            String gameDir,
            String choosingPlayer,
            boolean skipInitShuffling,
            int winsNeeded
    ) throws Exception {
        // Create a minimal test deck for bot slots (headless players bring their own decks)
        String testDeckFile = "test.dck";
        File f = new File(testDeckFile);
        if (!f.exists()) {
            testDeckFile = DeckUtil.writeTextToTempFile(""
                    + "5 Swamp" + System.lineSeparator()
                    + "5 Forest" + System.lineSeparator()
                    + "5 Island" + System.lineSeparator()
                    + "5 Mountain" + System.lineSeparator()
                    + "5 Plains");
        }
        DeckCardLists testDeck = DeckImporter.importDeckFromFile(testDeckFile, false);

        int numPlayers = config.getPlayers().size();
        String gameTypeStr = config.getGameType() != null ? config.getGameType() : "Two Player Duel";
        String deckTypeStr = config.getDeckType() != null ? config.getDeckType() : "Constructed - Legacy";

        MatchOptions options = new MatchOptions("AI Puppeteer", gameTypeStr, numPlayers > 2);
        for (AiPuppeteerConfig.PlayerConfig player : config.getPlayers()) {
            options.getPlayerTypes().add(player.getPlayerType());
        }
        options.setDeckType(deckTypeStr);
        options.setAttackOption(MultiplayerAttackOption.MULTIPLE);
        options.setRange(RangeOfInfluence.ALL);
        options.setWinsNeeded(winsNeeded);
        options.setMatchTimeLimit(MatchTimeLimit.NONE);
        options.setMatchBufferTime(MatchBufferTime.NONE);
        if (skipInitShuffling) {
            options.setSkipInitShuffling(true);
        }
        if (choosingPlayer != null && !choosingPlayer.isEmpty()) {
            options.setChoosingPlayerName(choosingPlayer);
        }
        options.setFreeMulligans(gameTypeStr.toLowerCase().contains("commander") ? 1 : 0);
        options.setSkillLevel(SkillLevel.CASUAL);
        options.setRollbackTurnsAllowed(true);
        options.setQuitRatio(100);
        options.setMinimumRating(0);
        options.setSpectatorsAllowed(true);
        String serverAddress = SessionHandler.getSession().getServerHost();
        options.setBannedUsers(IgnoreList.getIgnoredUsers(serverAddress));
        options.setGameLogDir(gameDir);

        TableView table = SessionHandler.createTable(roomId, options);
        LOGGER.info("keepAlive: created table " + table.getTableId());

        // Join players to the table
        int deckIndex = 0;
        for (AiPuppeteerConfig.PlayerConfig player : config.getPlayers()) {
            String name = player.name != null ? player.name : ("Player " + (deckIndex + 1));
            PlayerType playerType = player.getPlayerType();

            DeckCardLists deckToUse;
            if (player.deck != null && !player.deck.isEmpty()) {
                File deckFile = new File(player.deck);
                if (!deckFile.exists()) {
                    deckFile = new File("../" + player.deck);
                }
                assert deckFile.exists() : "keepAlive: deck file not found: " + player.deck;
                deckToUse = DeckImporter.importDeckFromFile(deckFile.getPath(), false);
            } else {
                deckToUse = testDeck;
            }

            if (player.isHeadless()) {
                LOGGER.info("keepAlive: slot reserved for headless client: " + name);
            } else {
                boolean joined = SessionHandler.joinTable(roomId, table.getTableId(), name, playerType, 1, deckToUse, "");
                LOGGER.info("keepAlive: joined " + name + " (" + playerType + ") -> " + joined);
            }
            if (player.isBot()) {
                deckIndex++;
            }
        }

        // Start match or wait for bridge clients
        if (config.getBridgeCount() == 0) {
            SessionHandler.startMatch(roomId, table.getTableId());
        } else {
            LOGGER.info("AI Puppeteer: waiting for " + config.getBridgeCount()
                    + " bridge client(s) to join table " + table.getTableId()
                    + " gameDir=" + gameDir);
            if (healthServer != null) {
                healthServer.signalGameReady(gameDir, table.getTableId().toString());
            }
            final UUID finalTableId = table.getTableId();
            Thread starter = new Thread(() -> {
                long deadline = System.currentTimeMillis() + TimeUnit.SECONDS.toMillis(600);
                while (System.currentTimeMillis() < deadline) {
                    try {
                        Collection<TableView> tables = SessionHandler.getTables(roomId);
                        for (TableView tv : tables) {
                            if (finalTableId.equals(tv.getTableId())) {
                                if (tv.getTableState() == TableState.READY_TO_START) {
                                    LOGGER.info("keepAlive: all players joined, starting match for table " + finalTableId);
                                    SessionHandler.startMatch(roomId, finalTableId);
                                    return;
                                }
                                break;
                            }
                        }
                        Thread.sleep(1000);
                    } catch (Exception e) {
                        LOGGER.warn("keepAlive: error polling for ready state", e);
                    }
                }
                LOGGER.error("keepAlive: timed out waiting for bridge clients (600s)");
            }, "KeepAlive-MatchStarter");
            starter.setDaemon(true);
            starter.start();
        }

        return table.getTableId();
    }

    /**
     * Poll for a game to start on the given table, then auto-watch it.
     */
    private void watchForGameStart(UUID roomId, UUID tableId) {
        Thread watcher = new Thread(() -> {
            long deadline = System.currentTimeMillis() + TimeUnit.SECONDS.toMillis(600);
            while (System.currentTimeMillis() < deadline) {
                Collection<TableView> tables = SessionHandler.getTables(roomId);
                for (TableView tableView : tables) {
                    if (!tableId.equals(tableView.getTableId())) {
                        continue;
                    }
                    if (TableState.DUELING.equals(tableView.getTableState())) {
                        LOGGER.info("keepAlive: auto-watching table " + tableId);
                        SessionHandler.watchTable(roomId, tableId);
                        return;
                    }
                }
                try {
                    Thread.sleep(1000);
                } catch (InterruptedException ex) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
            LOGGER.warn("keepAlive: auto-watch timed out for table " + tableId);
        }, "KeepAlive-AutoWatch");
        watcher.setDaemon(true);
        watcher.start();
    }
}
