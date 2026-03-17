package mage.client.observer;

import mage.cards.repository.CardScanner;
import mage.client.MageFrame;
import mage.client.dialog.PreferencesDialog;
import mage.client.util.EDTExceptionHandler;
import org.apache.log4j.Logger;

import javax.swing.*;
import java.awt.*;
import java.io.IOException;
import java.net.BindException;
import java.nio.charset.Charset;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;

/**
 * Entry point for the observer-optimized XMage client.
 *
 * This creates a ObserverMageFrame instead of a regular MageFrame,
 * which uses ObserverGamePane/ObserverGamePanel for watching games.
 * The observer panel automatically requests hand permission from all players.
 *
 * Usage:
 *   java -jar mage-client-observer.jar [standard XMage client args]
 *
 * Or via Maven:
 *   mvn exec:java -pl Mage.Client.Observer
 *
 * For AI puppeteer integration, use the standard AI puppeteer env vars:
 *   XMAGE_AI_PUPPETEER=1              - Enable AI puppeteer mode
 *   XMAGE_AI_PUPPETEER_SERVER         - Server address
 *   XMAGE_AI_PUPPETEER_PORT           - Server port
 *   XMAGE_AI_PUPPETEER_USER           - Username
 *
 * The lobby UI is automatically hidden in observer mode. In AI puppeteer mode,
 * game creation and auto-watch are handled by the standard TablesPanel logic.
 */
public class ObserverMain {

    private static final Logger LOGGER = Logger.getLogger(ObserverMain.class);

    static ObserverHealthServer startConfiguredHealthServer() {
        int healthPort = Integer.getInteger("xmage.observer.healthPort", 0);
        if (healthPort <= 0) {
            return null;
        }
        String healthPortFile = System.getProperty("xmage.observer.healthPortFile");
        int maxRetries = 100;
        RuntimeException lastException = null;
        for (int i = 0; i < maxRetries; i++) {
            int candidatePort = healthPort + i;
            ObserverHealthServer server;
            try {
                server = startHealthServer(candidatePort);
            } catch (RuntimeException e) {
                if (!(e.getCause() instanceof BindException)) {
                    throw e;
                }
                lastException = e;
                LOGGER.debug("Port " + candidatePort + " busy, trying next");
                continue;
            }
            if (candidatePort != healthPort) {
                LOGGER.info("Health port " + healthPort + " was busy, bound to " + candidatePort + " instead");
            }
            if (healthPortFile != null) {
                writePortFile(healthPortFile, server.getPort());
            }
            return server;
        }
        throw new RuntimeException(
                "Failed to bind observer health server on any port in range "
                        + healthPort + "-" + (healthPort + maxRetries - 1), lastException);
    }

    static ObserverHealthServer startHealthServer(int healthPort) {
        try {
            ObserverHealthServer healthServer = new ObserverHealthServer(healthPort);
            healthServer.start();
            return healthServer;
        } catch (IOException e) {
            throw new RuntimeException("Failed to start observer health server on port " + healthPort, e);
        }
    }

    private static void writePortFile(String path, int port) {
        Path target = Paths.get(path);
        Path tmp = target.resolveSibling(target.getFileName() + ".tmp");
        try {
            Files.writeString(tmp, Integer.toString(port) + "\n", StandardCharsets.UTF_8);
            Files.move(tmp, target, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) {
            throw new RuntimeException("Failed to write health port file: " + path, e);
        }
    }

    public static void main(final String[] args) {
        // Same setup as MageFrame.main()
        System.setProperty("java.util.Arrays.useLegacyMergeSort", "true");
        LOGGER.info("Starting MAGE OBSERVER CLIENT");
        try {
            java.net.URL classUrl = ObserverMain.class.getResource("ObserverMain.class");
            if (classUrl != null && "file".equals(classUrl.getProtocol())) {
                long mtime = new java.io.File(classUrl.toURI()).lastModified();
                LOGGER.info("Build: " + new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss").format(new java.util.Date(mtime)));
            }
        } catch (Exception ignored) {}
        LOGGER.info("Java version: " + System.getProperty("java.version"));
        LOGGER.info("Logging level: " + LOGGER.getEffectiveLevel());
        LOGGER.info("Default charset: " + Charset.defaultCharset());

        Thread.setDefaultUncaughtExceptionHandler((t, e) -> LOGGER.fatal(null, e));

        // Skip bulk card database scanning. The MageFrame constructor calls
        // CardScanner.scan() on empty DBs, which loads all ~30K card classes
        // and consumes hundreds of MB of metaspace. The observer only needs
        // cards from the decks it imports — CardRepository.findCard() lazily
        // loads individual cards on demand.
        CardScanner.scanned = true;

        SwingUtilities.invokeLater(() -> {
            // Parse command line args
            boolean liteMode = false;
            for (String arg : args) {
                if (arg.startsWith("-lite")) {
                    liteMode = true;
                }
            }

            boolean noWindow = Boolean.getBoolean("xmage.observer.noWindow");

            // Show splash unless in lite mode or noWindow mode.
            // SplashScreen.getSplashScreen() throws HeadlessException without a
            // display, so skip it entirely when noWindow is set (golden tests
            // run under xvfb but there's no splash JAR manifest anyway).
            if (!liteMode && !noWindow) {
                final SplashScreen splash = SplashScreen.getSplashScreen();
                if (splash != null) {
                    Graphics2D g2 = splash.createGraphics();
                    try {
                        g2.setComposite(AlphaComposite.Clear);
                        g2.fillRect(120, 140, 200, 40);
                        g2.setPaintMode();
                        g2.setColor(Color.white);
                        g2.drawString("Observer Mode", 560, 460);
                    } finally {
                        g2.dispose();
                    }
                    splash.update();
                }
            }

            // Auto-update settings if needed (same as MageFrame).
            // In noWindow mode (golden tests), skip screen-dependent settings —
            // DPI and screen size are meaningless for a headless observer.
            if (!noWindow) {
                int settingsVersion = PreferencesDialog.getCachedValue(PreferencesDialog.KEY_SETTINGS_VERSION, 0);
                if (settingsVersion == 0) {
                    LOGGER.info("Settings: first run, applying GUI size settings");
                    int screenDPI = Toolkit.getDefaultToolkit().getScreenResolution();
                    int screenHeight = Toolkit.getDefaultToolkit().getScreenSize().height;
                    String preset = PreferencesDialog.getDefaultSizeSettings().findBestPreset(screenDPI, screenHeight);
                    if (preset != null) {
                        LOGGER.info("Settings: selected preset " + preset);
                        PreferencesDialog.getDefaultSizeSettings().applyPreset(preset);
                    }
                    PreferencesDialog.saveValue(PreferencesDialog.KEY_SETTINGS_VERSION, String.valueOf(1));
                }
            }

            // Disable macOS fullscreen toggle — it grabs focus and we never want that.
            // MageFrame.main() reads this from -Dxmage.fullScreen but ObserverMain
            // doesn't go through MageFrame.main(), so the static field stays true.
            try {
                java.lang.reflect.Field f = MageFrame.class.getDeclaredField("macOsFullScreenEnabled");
                f.setAccessible(true);
                f.setBoolean(null, false);
            } catch (NoSuchFieldException | IllegalAccessException e) {
                LOGGER.warn("Could not disable macOS fullscreen: " + e.getMessage());
            }

            // Create the observer frame (instead of regular MageFrame)
            try {
                ObserverHealthServer healthServer = null;
                if (Boolean.getBoolean("xmage.observer.keepAlive")) {
                    // Bind the health endpoint before MageFrame cold-start work
                    // so the harness can long-poll readiness during startup.
                    healthServer = startConfiguredHealthServer();
                }
                ObserverMageFrame observerFrame = new ObserverMageFrame();
                if (healthServer != null) {
                    observerFrame.setHealthServer(healthServer);
                }
                ObserverMageFrame.setInstance(observerFrame);
                EDTExceptionHandler.registerMainApp(observerFrame);
                // Prevent the observer window from ever stealing OS keyboard focus.
                // setFocusableWindowState(false) marks this as a non-focusable window
                // (like a floating palette). Internal JInternalFrame dialogs (card
                // reveals, pick choices) call setSelected(true) which triggers
                // requestFocus() that bubbles up to the parent JFrame and steals
                // focus from the user's active window. This is the only reliable fix —
                // overriding toFront() alone doesn't prevent these internal focus
                // requests.
                // The window can still be clicked to raise it (window manager handles
                // raising separately from keyboard focus), but it won't receive
                // keyboard input — which is fine for an observer.
                observerFrame.setFocusableWindowState(false);
                observerFrame.setAutoRequestFocus(false);
                // dispose() destroys the native peer created by the
                // constructor's pack(), allowing setType() (which requires a
                // non-displayable window). Recreating the peer as UTILITY
                // prevents Linux WMs from auto-raising/focusing the window.
                observerFrame.dispose();
                observerFrame.setType(Window.Type.UTILITY);
                if (Boolean.getBoolean("xmage.observer.noWindow")) {
                    // Golden tests: displayable but never mapped (shown).
                    observerFrame.setLocation(-10000, -10000);
                    observerFrame.addNotify();
                } else {
                    observerFrame.setVisible(true);
                    observerFrame.toBack();
                }
                LOGGER.info("Observer client started successfully");

                // In keepAlive mode, start the stdin command loop.
                // The loop reads game configs from stdin; actual "ready" signal
                // is logged later when prepareAndShowServerLobby() completes.
                if (Boolean.getBoolean("xmage.observer.keepAlive")) {
                    observerFrame.startKeepAliveLoop();
                }
            } catch (Throwable e) {
                LOGGER.fatal("Critical error on start up: " + e.getMessage(), e);
                System.exit(1);
            }
        });
    }
}
