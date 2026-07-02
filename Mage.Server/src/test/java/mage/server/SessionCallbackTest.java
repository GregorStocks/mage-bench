package mage.server;

import mage.MageException;
import mage.interfaces.callback.ClientCallback;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.players.net.UserData;
import mage.server.managers.ChatManager;
import mage.server.managers.ConfigSettings;
import mage.server.managers.DraftManager;
import mage.server.managers.GameManager;
import mage.server.managers.GamesRoomManager;
import mage.server.managers.MailClient;
import mage.server.managers.ManagerFactory;
import mage.server.managers.ReplayManager;
import mage.server.managers.SessionManager;
import mage.server.managers.TableManager;
import mage.server.managers.ThreadExecutor;
import mage.server.managers.TournamentManager;
import mage.server.managers.UserManager;
import org.jboss.remoting.callback.AsynchInvokerCallbackHandler;
import org.jboss.remoting.callback.Callback;
import org.jboss.remoting.callback.HandleCallbackException;
import org.jboss.remoting.callback.InvokerCallbackHandler;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Optional;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Session.fireCallback must never drop callbacks under contention: it enqueues onto a
 * per-session writer thread that delivers in order (see issue
 * firecallback-50ms-silent-drop — the old implementation dropped callbacks after a
 * 50ms lock timeout).
 */
public class SessionCallbackTest {

    /**
     * Callback handler stub: records delivered callbacks, can block deliveries to simulate a
     * slow client, can throw to simulate a broken connection.
     */
    private static class RecordingCallbackHandler implements AsynchInvokerCallbackHandler {

        final List<ClientCallback> delivered = new CopyOnWriteArrayList<>();
        volatile long delayFirstCallbackMs = 0;
        volatile boolean failAllCallbacks = false;
        private boolean firstCallback = true;

        @Override
        public void handleCallbackOneway(Callback callback, boolean serverSide) throws HandleCallbackException {
            if (failAllCallbacks) {
                throw new HandleCallbackException("simulated connection failure");
            }
            if (firstCallback) {
                firstCallback = false;
                if (delayFirstCallbackMs > 0) {
                    try {
                        Thread.sleep(delayFirstCallbackMs);
                    } catch (InterruptedException ex) {
                        Thread.currentThread().interrupt();
                        throw new HandleCallbackException("interrupted", ex);
                    }
                }
            }
            delivered.add((ClientCallback) callback.getParameter());
        }

        @Override
        public void handleCallbackOneway(Callback callback) throws HandleCallbackException {
            handleCallbackOneway(callback, false);
        }

        @Override
        public void handleCallback(Callback callback, boolean asynch, boolean serverSide) throws HandleCallbackException {
            handleCallbackOneway(callback, serverSide);
        }

        @Override
        public void handleCallback(Callback callback) throws HandleCallbackException {
            handleCallbackOneway(callback, false);
        }
    }

    /**
     * ManagerFactory stub: only sessionManager() is used by the session's callback error path.
     */
    private static class StubManagerFactory implements ManagerFactory {

        final AtomicReference<DisconnectReason> disconnectReason = new AtomicReference<>();
        final CountDownLatch disconnected = new CountDownLatch(1);

        @Override
        public SessionManager sessionManager() {
            return new SessionManager() {
                @Override
                public void disconnect(String sessionId, DisconnectReason reason, boolean checkUserDisconnection) {
                    disconnectReason.set(reason);
                    disconnected.countDown();
                }

                @Override
                public Optional<Session> getSession(String sessionId) {
                    throw new UnsupportedOperationException();
                }

                @Override
                public void createSession(String sessionId, InvokerCallbackHandler callbackHandler) {
                    throw new UnsupportedOperationException();
                }

                @Override
                public boolean registerUser(String sessionId, String userName, String password, String email) throws MageException {
                    throw new UnsupportedOperationException();
                }

                @Override
                public boolean connectUser(String sessionId, String restoreSessionId, String userName, String password, String userInfo, boolean detailsMode) throws MageException {
                    throw new UnsupportedOperationException();
                }

                @Override
                public boolean connectAdmin(String sessionId) {
                    throw new UnsupportedOperationException();
                }

                @Override
                public boolean setUserData(String userName, String sessionId, UserData userData, String clientVersion, String userIdStr) throws MageException {
                    throw new UnsupportedOperationException();
                }

                @Override
                public void disconnectAnother(String sessionId, String userSessionId) {
                    throw new UnsupportedOperationException();
                }

                @Override
                public boolean checkAdminAccess(String sessionId) {
                    throw new UnsupportedOperationException();
                }

                @Override
                public boolean isValidSession(String sessionId) {
                    throw new UnsupportedOperationException();
                }

                @Override
                public Optional<User> getUser(String sessionId) {
                    throw new UnsupportedOperationException();
                }

                @Override
                public boolean extendUserSession(String sessionId, String pingInfo) {
                    throw new UnsupportedOperationException();
                }

                @Override
                public void sendErrorMessageToClient(String sessionId, String message) {
                    throw new UnsupportedOperationException();
                }

                @Override
                public void checkHealth() {
                    throw new UnsupportedOperationException();
                }
            };
        }

        @Override
        public ChatManager chatManager() {
            throw new UnsupportedOperationException();
        }

        @Override
        public DraftManager draftManager() {
            throw new UnsupportedOperationException();
        }

        @Override
        public GameManager gameManager() {
            throw new UnsupportedOperationException();
        }

        @Override
        public GamesRoomManager gamesRoomManager() {
            throw new UnsupportedOperationException();
        }

        @Override
        public MailClient mailClient() {
            throw new UnsupportedOperationException();
        }

        @Override
        public MailClient mailgunClient() {
            throw new UnsupportedOperationException();
        }

        @Override
        public ReplayManager replayManager() {
            throw new UnsupportedOperationException();
        }

        @Override
        public TableManager tableManager() {
            throw new UnsupportedOperationException();
        }

        @Override
        public UserManager userManager() {
            throw new UnsupportedOperationException();
        }

        @Override
        public ConfigSettings configSettings() {
            throw new UnsupportedOperationException();
        }

        @Override
        public ThreadExecutor threadExecutor() {
            throw new UnsupportedOperationException();
        }

        @Override
        public TournamentManager tournamentManager() {
            throw new UnsupportedOperationException();
        }
    }

    private static ClientCallback newCallback() {
        return new ClientCallback(ClientCallbackMethod.GAME_UPDATE, null, null);
    }

    private static void awaitDelivered(RecordingCallbackHandler handler, int count) throws InterruptedException {
        long deadline = System.currentTimeMillis() + 10_000;
        while (handler.delivered.size() < count) {
            assertThat(System.currentTimeMillis())
                    .as("timed out waiting for %d callbacks, got %d", count, handler.delivered.size())
                    .isLessThan(deadline);
            Thread.sleep(5);
        }
    }

    @Test
    @DisplayName("slow delivery must not drop later callbacks (old code dropped after 50ms lock timeout)")
    void slowDeliveryDoesNotDropCallbacks() throws InterruptedException {
        RecordingCallbackHandler handler = new RecordingCallbackHandler();
        handler.delayFirstCallbackMs = 200; // well above the old 50ms drop threshold
        Session session = new Session(new StubManagerFactory(), "test-session", handler);
        try {
            int total = 5;
            for (int i = 0; i < total; i++) {
                session.fireCallback(newCallback());
            }
            awaitDelivered(handler, total);
            assertThat(handler.delivered).hasSize(total);
            assertThat(handler.delivered)
                    .extracting(ClientCallback::getMessageId)
                    .containsExactly(1, 2, 3, 4, 5);
        } finally {
            session.shutdown();
        }
    }

    @Test
    @DisplayName("callbacks queued before shutdown still drain, callbacks after shutdown are rejected")
    void shutdownDrainsQueueAndRejectsNewCallbacks() throws InterruptedException {
        RecordingCallbackHandler handler = new RecordingCallbackHandler();
        handler.delayFirstCallbackMs = 100;
        Session session = new Session(new StubManagerFactory(), "test-session", handler);

        int total = 3;
        for (int i = 0; i < total; i++) {
            session.fireCallback(newCallback());
        }
        session.shutdown();
        awaitDelivered(handler, total);

        session.fireCallback(newCallback());
        Thread.sleep(50);
        assertThat(handler.delivered).hasSize(total);
    }

    @Test
    @DisplayName("delivery failure invalidates the session and disconnects it")
    void deliveryFailureDisconnectsSession() throws InterruptedException {
        RecordingCallbackHandler handler = new RecordingCallbackHandler();
        handler.failAllCallbacks = true;
        StubManagerFactory managerFactory = new StubManagerFactory();
        Session session = new Session(managerFactory, "test-session", handler);
        try {
            session.fireCallback(newCallback());
            assertThat(managerFactory.disconnected.await(10, TimeUnit.SECONDS)).isTrue();
            assertThat(managerFactory.disconnectReason.get()).isEqualTo(DisconnectReason.LostConnection);

            // session is invalid now: further callbacks never reach the handler
            handler.failAllCallbacks = false;
            session.fireCallback(newCallback());
            Thread.sleep(50);
            assertThat(handler.delivered).isEmpty();
        } finally {
            session.shutdown();
        }
    }
}
