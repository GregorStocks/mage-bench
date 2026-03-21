package mage.client.observer;

import mage.client.MageFrame;
import mage.client.MagePane;
import mage.client.chat.ChatPanelBasic;
import mage.client.dialog.PreferencesDialog;
import mage.client.game.GamePanel;
import mage.client.game.PlayAreaPanelOptions;
import mage.client.observer.recording.FFmpegEncoder;
import mage.client.observer.recording.FrameCaptureService;
import mage.view.GameView;
import mage.view.PlayerView;
import org.apache.log4j.Logger;

import javax.swing.*;
import java.io.Serializable;
import java.lang.reflect.Field;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * observer-optimized game panel that automatically requests hand permission
 * from all players when watching a game, and displays all visible hands
 * directly in each player's play area.
 */
public class ObserverGamePanel extends GamePanel {

    private static final Logger logger = Logger.getLogger(ObserverGamePanel.class);

    private final RoundTracker roundTracker = new RoundTracker();
    private final ObserverLayoutManager layoutManager = new ObserverLayoutManager();
    private final ObserverHandManager handManager = new ObserverHandManager();
    private final ObserverZonePanelManager zonePanelManager = new ObserverZonePanelManager();
    private final ObserverPlayerPanelStyler playerPanelStyler = new ObserverPlayerPanelStyler();
    private final ObserverCostDisplay costDisplay = new ObserverCostDisplay();
    private final ObserverGameEventLogger gameEventLogger = new ObserverGameEventLogger();

    private FrameCaptureService frameCaptureService;
    private Path recordingPath;
    private Thread shutdownHook;

    private CombinedChatPanel combinedChatPanel;
    private boolean chatPanelReplaced = false;

    private Path gameDirPath;
    private boolean watchingSignaled = false;

    private ObserverHealthServer healthServer;

    /** Set the health server so game-end can be signaled via HTTP. */
    public void setHealthServer(ObserverHealthServer healthServer) {
        this.healthServer = healthServer;
    }

    private Path requireConfiguredGameDirPath(String source) {
        if (gameDirPath != null) {
            return gameDirPath;
        }
        String gameDirStr = System.getProperty("xmage.observer.gameDir");
        if (gameDirStr == null || gameDirStr.isEmpty()) {
            throw new IllegalStateException(
                    source + ": xmage.observer.gameDir must be configured before watching a game"
            );
        }
        gameDirPath = Paths.get(gameDirStr);
        return gameDirPath;
    }

    private void signalWatchingReady() {
        if (watchingSignaled || healthServer == null) {
            return;
        }
        healthServer.signalGameWatching(requireConfiguredGameDirPath("signalWatchingReady").toString());
        watchingSignaled = true;
    }

    /**
     * The golden harness must not start replay until the observer has issued its
     * initial hand-permission requests for the current GameView. Signaling
     * readiness from watchGame() is too early and can interleave those dialogs
     * with the first replay decisions, but keepAlive spectators can also miss the
     * init(GameView) path entirely. Request permissions and complete the health
     * signal from every callback path that carries a real GameView instead.
     */
    private void requestPermissionsAndSignalReady(GameView game) {
        handManager.requestHandPermissions(game);
        signalWatchingReady();
    }

    @Override
    public synchronized void watchGame(UUID currentTableId, UUID parentTableId, UUID gameId, MagePane gamePane) {
        if (healthServer != null) {
            requireConfiguredGameDirPath("watchGame");
        } else {
            String gameDirStr = System.getProperty("xmage.observer.gameDir");
            if (gameDirStr != null && !gameDirStr.isEmpty()) {
                gameDirPath = Paths.get(gameDirStr);
            }
        }

        watchingSignaled = false;
        handManager.resetForGame(gameId);
        replaceChatWithCombinedPanel();
        super.watchGame(currentTableId, parentTableId, gameId, gamePane);
    }

    /**
     * Replace the default chat panels with observer-optimized versions.
     * Player chat (top) is kept separate from game log (bottom, with spam filtering).
     * This must be called BEFORE super.watchGame() which connects the chat to the server.
     */
    private void replaceChatWithCombinedPanel() {
        if (chatPanelReplaced) {
            return;
        }

        try {
            var playerChatPanel = new ChatPanelBasic();
            playerChatPanel.useExtendedView(ChatPanelBasic.VIEW_MODE.GAME);
            playerChatPanel.disableInput();

            combinedChatPanel = new CombinedChatPanel();
            combinedChatPanel.setPlayerChatPanel(playerChatPanel);
            combinedChatPanel.setRoundTracker(roundTracker);
            combinedChatPanel.setGamePanel(this);

            Field gameChatField = GamePanel.class.getDeclaredField("gameChatPanel");
            gameChatField.setAccessible(true);
            Field userChatField = GamePanel.class.getDeclaredField("userChatPanel");
            userChatField.setAccessible(true);

            gameChatField.set(this, combinedChatPanel);
            userChatField.set(this, playerChatPanel);

            Field splitChatField = GamePanel.class.getDeclaredField("splitChatAndLogs");
            splitChatField.setAccessible(true);
            JSplitPane splitChat = (JSplitPane) splitChatField.get(this);
            if (splitChat != null) {
                splitChat.setTopComponent(playerChatPanel);
                splitChat.setBottomComponent(combinedChatPanel);
                splitChat.setResizeWeight(0.5);
            }

            layoutManager.stripChatHoverEffects(playerChatPanel);
            layoutManager.stripChatHoverEffects(combinedChatPanel);

            chatPanelReplaced = true;
        } catch (NoSuchFieldException | IllegalAccessException e) {
            logger.warn("Failed to setup chat panels", e);
        }
    }

    @Override
    public synchronized void init(int messageId, GameView game, boolean callGameUpdateAfterInit) {
        ToolTipManager.sharedInstance().setEnabled(false);
        PreferencesDialog.saveValue(PreferencesDialog.KEY_SHOW_TOOLTIPS_DELAY, "0");
        layoutManager.adjustBattlefieldCardSizes(this);
        super.init(messageId, game, callGameUpdateAfterInit);
        roundTracker.update(game);
        updateFrameGameName();
        layoutManager.hideHandContainer(this);
        requestPermissionsAndSignalReady(game);
        initCostPolling();
        initGameEventLog();
        playerPanelStyler.initializePlayerColors(game);
        playerPanelStyler.stylePlayerPanels(getPlayers());
        playerPanelStyler.updatePlayerHighlights(game, getPlayers());
        layoutManager.schedulePopupDismissal(this);
        gameEventLogger.writeStateSnapshotIfChanged(game, roundTracker, getLoadedCards());
    }

    private void updateFrameGameName() {
        String gameDirStr = System.getProperty("xmage.observer.gameDir");
        if (gameDirStr == null || gameDirStr.isEmpty()) {
            return;
        }
        MageFrame frame = MageFrame.getInstance();
        if (frame instanceof ObserverMageFrame observerMageFrame) {
            String gameName = Paths.get(gameDirStr).getFileName().toString();
            observerMageFrame.setGameName(gameName);
        }
    }

    @Override
    public synchronized void updateGame(
            int messageId,
            GameView game,
            boolean showPlayable,
            Map<String, Serializable> options,
            Set<UUID> targets
    ) {
        super.updateGame(messageId, game, showPlayable, options, targets);
        roundTracker.update(game);
        requestPermissionsAndSignalReady(game);
        gameEventLogger.writeStateSnapshotIfChanged(game, roundTracker, getLoadedCards());
    }

    @Override
    public synchronized void updateGame(int messageId, GameView game) {
        super.updateGame(messageId, game);
        layoutManager.schedulePopupDismissal(this);
        layoutManager.hideHandContainer(this);
        handManager.distributeHands(game, getPlayers(), getLoadedCards(), getBigCard(), getGameId());
        zonePanelManager.injectZonePanels(game, getPlayers());
        zonePanelManager.distributeGraveyards(game, getBigCard(), getGameId());
        zonePanelManager.distributeExile(game, getBigCard(), getGameId());
        zonePanelManager.distributeCommanders(game, getBigCard(), getGameId());
        zonePanelManager.replaceAvatarsWithCommanderArt(game, getPlayers());
        playerPanelStyler.updatePlayerPanelVisibility(game, getPlayers(), costDisplay);
        playerPanelStyler.updatePlayerHighlights(game, getPlayers());
        layoutManager.relayoutStackVertically(this);
    }

    @Override
    public synchronized void updateGame() {
        super.updateGame();
        layoutManager.restoreDeadPlayerPanelSizes(getPlayers());
    }

    @Override
    public void endMessage(int messageId, GameView gameView, Map<String, Serializable> options, String message) {
        super.endMessage(messageId, gameView, options, message);

        gameEventLogger.logGameOver(message);

        if (healthServer != null && gameDirPath != null) {
            healthServer.signalGameEnd(gameDirPath.toString());
        }

        costDisplay.stop();

        if (Boolean.getBoolean("xmage.observer.keepAlive")) {
            logger.info("Game ended (keepAlive mode, staying alive for next game)");
        } else {
            logger.info("Game ended, will auto-close in 10 seconds");
            var exitTimer = new Timer(10000, e -> {
                logger.info("Auto-closing observer spectator");
                System.exit(0);
            });
            exitTimer.setRepeats(false);
            exitTimer.start();
        }
    }

    /**
     * Override to enable showHandInPlayArea, showGraveyardInPlayArea, and showExileInPlayArea for all players in observer mode.
     */
    @Override
    protected PlayAreaPanelOptions createPlayAreaPanelOptions(
            GameView game,
            PlayerView player,
            boolean playerItself,
            boolean topRow
    ) {
        logger.info("Creating PlayAreaPanelOptions for " + player.getName() + " with showExileInPlayArea=true");
        return new PlayAreaPanelOptions(
                game.isPlayer(),
                player.isHuman(),
                playerItself,
                game.isRollbackTurnsAllowed(),
                topRow,
                true,
                true,
                true
        );
    }

    /**
     * Override to suppress exile popup windows in observer mode.
     * Exile is displayed inline in each player's play area instead.
     */
    @Override
    protected void updateExileWindows(GameView game) {
    }

    @Override
    public void onActivated() {
        layoutManager.removeSplitterFromRestore(this);
        super.onActivated();
    }

    void logChatEvent(String type, String message, String username) {
        gameEventLogger.logChatEvent(type, message, username);
    }

    private void initCostPolling() {
        if (gameDirPath == null) {
            String gameDirStr = System.getProperty("xmage.observer.gameDir");
            if (gameDirStr != null && !gameDirStr.isEmpty()) {
                gameDirPath = Paths.get(gameDirStr);
            }
        }
        costDisplay.init(gameDirPath, System.getenv("XMAGE_AI_PUPPETEER_PLAYERS_CONFIG"));
    }

    private void initGameEventLog() {
        if (gameDirPath == null) {
            String gameDirStr = System.getProperty("xmage.observer.gameDir");
            if (gameDirStr != null && !gameDirStr.isEmpty()) {
                gameDirPath = Paths.get(gameDirStr);
            }
        }
        gameEventLogger.init(gameDirPath);
    }

    /**
     * Start recording the game panel to a video file.
     *
     * @param outputPath Path to the output video file (.mov)
     */
    public void startRecording(Path outputPath) {
        if (frameCaptureService != null && frameCaptureService.isRunning()) {
            logger.warn("Recording already in progress");
            return;
        }

        this.recordingPath = outputPath;
        var encoder = new FFmpegEncoder(outputPath);
        frameCaptureService = new FrameCaptureService(this, 30, encoder);
        frameCaptureService.start();

        shutdownHook = new Thread(() -> {
            logger.info("Shutdown hook: stopping recording");
            if (frameCaptureService != null) {
                frameCaptureService.stop();
            }
        }, "RecordingShutdownHook");
        Runtime.getRuntime().addShutdownHook(shutdownHook);
    }

    /**
     * Stop recording if in progress.
     */
    public void stopRecording() {
        if (shutdownHook != null) {
            try {
                Runtime.getRuntime().removeShutdownHook(shutdownHook);
            } catch (IllegalStateException e) {
                // JVM is already shutting down, hook will run anyway
            }
            shutdownHook = null;
        }

        if (frameCaptureService != null) {
            frameCaptureService.stop();
            frameCaptureService = null;
            logger.info("Recording stopped: " + recordingPath);
        }
    }

    /**
     * Check if recording is currently active.
     */
    public boolean isRecording() {
        return frameCaptureService != null && frameCaptureService.isRunning();
    }
}
