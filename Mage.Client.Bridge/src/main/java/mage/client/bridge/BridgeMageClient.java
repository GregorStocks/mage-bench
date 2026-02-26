package mage.client.bridge;

import mage.interfaces.MageClient;
import mage.interfaces.callback.ClientCallback;
import mage.remote.Session;
import mage.utils.MageVersion;
import org.apache.log4j.Logger;

/**
 * Bridge MageClient implementation for bot/AI players.
 * Delegates callback handling to BridgeCallbackHandler.
 */
public class BridgeMageClient implements MageClient {

    private static final Logger logger = Logger.getLogger(BridgeMageClient.class);
    private static final MageVersion version = new MageVersion(BridgeMageClient.class);

    private final String username;
    private Session session;
    private volatile BridgeCallbackHandler callbackHandler;
    private volatile boolean running = true;
    private volatile boolean reconnectable = false;
    private volatile boolean suppressDisconnect = false;

    public BridgeMageClient(String username) {
        this.username = username;
        this.callbackHandler = new BridgeCallbackHandler(this);
    }

    public void setSession(Session session) {
        this.session = session;
        this.callbackHandler.setSession(session);
    }

    public Session getSession() {
        return session;
    }

    public String getUsername() {
        return username;
    }

    public BridgeCallbackHandler getCallbackHandler() {
        return callbackHandler;
    }

    public void setCallbackHandler(BridgeCallbackHandler handler) {
        this.callbackHandler = handler;
    }

    public boolean isRunning() {
        return running;
    }

    public void stop() {
        running = false;
    }

    public void setRunning(boolean running) {
        this.running = running;
    }

    public boolean isReconnectable() {
        return reconnectable;
    }

    public void suppressDisconnectCallbacks(boolean suppress) {
        this.suppressDisconnect = suppress;
    }

    @Override
    public MageVersion getVersion() {
        return version;
    }

    @Override
    public void connected(String message) {
        logger.info("[" + username + "] Connected: " + message);
    }

    @Override
    public void disconnected(boolean askToReconnect, boolean keepMySessionActive) {
        if (suppressDisconnect) {
            logger.info("[" + username + "] Disconnected callback suppressed during reconnection");
            return;
        }
        logger.info("[" + username + "] Disconnected (askToReconnect=" + askToReconnect + ", keepSession=" + keepMySessionActive + ")");
        reconnectable = askToReconnect && keepMySessionActive;
        running = false;
    }

    @Override
    public void showMessage(String message) {
        logger.info("[" + username + "] Message: " + message);
    }

    @Override
    public void showError(String message) {
        logger.error("[" + username + "] Error: " + message);
    }

    @Override
    public void onNewConnection() {
        if (reconnectable) {
            logger.info("[" + username + "] Reconnected — preserving game state (skipping reset)");
            reconnectable = false;
        } else {
            logger.info("[" + username + "] New connection established");
            callbackHandler.reset();
        }
    }

    @Override
    public void onCallback(ClientCallback callback) {
        callbackHandler.handleCallback(callback);
    }
}
