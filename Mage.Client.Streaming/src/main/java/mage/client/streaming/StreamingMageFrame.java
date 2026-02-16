package mage.client.streaming;

import mage.MageException;
import mage.client.MageFrame;
import mage.client.MagePane;
import mage.client.SessionHandler;
import mage.client.game.GamePane;
import mage.client.preference.MagePreferences;
import mage.remote.Connection;
import org.apache.log4j.Logger;

import javax.swing.*;
import java.awt.*;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.lang.reflect.Field;
import java.net.SocketException;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.UUID;

/**
 * Streaming-optimized MageFrame that uses StreamingGamePane for watching games.
 * Skips the lobby UI and supports auto-watching a table via command-line args.
 */
public class StreamingMageFrame extends MageFrame {

    private static final Logger LOGGER = Logger.getLogger(StreamingMageFrame.class);
    private static final int MAX_RECONNECT_ATTEMPTS = 5;
    private static final int[] RECONNECT_BACKOFF_MS = {2000, 4000, 8000, 16000, 30000};
    private static final String GIT_BRANCH = getGitBranch();
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

    public StreamingMageFrame() throws MageException {
        super();
        // Start local overlay server early so mock/preview URLs are available before game start.
        LocalOverlayServer.getInstance();
        // Hide toolbar after initialization
        SwingUtilities.invokeLater(this::hideToolbar);
    }

    /**
     * Hide the main application toolbar since streaming spectators don't need it.
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
        }, "StreamingReconnect");
        reconnectThread.setDaemon(true);
        reconnectThread.start();
    }

    private static Connection buildConnectionFromPreferences() {
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
        return connection;
    }

    /**
     * Override watchGame to use StreamingGamePane instead of GamePane.
     */
    @Override
    public void watchGame(UUID currentTableId, UUID parentTableId, UUID gameId) {
        // Check if we're already watching this game
        for (Component component : getDesktop().getComponents()) {
            if (component instanceof StreamingGamePane
                    && ((StreamingGamePane) component).getGameId().equals(gameId)) {
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

        // Create streaming game pane
        StreamingGamePane gamePane = new StreamingGamePane();
        getDesktop().add(gamePane, JLayeredPane.DEFAULT_LAYER);
        gamePane.setVisible(true);
        gamePane.watchGame(currentTableId, parentTableId, gameId);
        setActive(gamePane);

        // Start recording if configured via system property
        String recordPath = System.getProperty("xmage.streaming.record");
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
        LOGGER.info("Streaming mode: hiding lobby UI");
        hideServerLobby();
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
}
