package mage.server.game;

import mage.constants.MultiplayerAttackOption;
import mage.constants.RangeOfInfluence;
import mage.game.Game;
import mage.game.TwoPlayerDuel;
import mage.game.mulligan.MulliganType;
import mage.server.game.GameSessionWatcher.GameSnapshot;
import mage.server.managers.ManagerFactory;
import mage.server.managers.ThreadExecutor;
import mage.util.ThreadUtils;
import mage.view.GameView;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Proxy;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * getGameView must never copy the live game off the game thread: an RPC thread (e.g. a
 * watcher attach) copying a game the game thread is concurrently mutating can throw
 * ConcurrentModificationException or produce a corrupt view (issue
 * watcher-getgameview-off-thread-copy). Off the game thread, views are built from the
 * last stable snapshot published by a game-thread build, and report the game seq
 * captured when that snapshot was published — game copies share the live seq counter,
 * so reading it live would pair old board state with a newer seq (game_seq drift).
 */
public class GameSessionWatcherThreadingTest {

    /**
     * TwoPlayerDuel that records the thread name of every copy() of the LIVE game.
     * super.copy() returns a plain TwoPlayerDuel, so snapshot copies are not recorded.
     */
    private static class CopyRecordingDuel extends TwoPlayerDuel {

        final List<String> copyThreads = new CopyOnWriteArrayList<>();

        CopyRecordingDuel() {
            super(MultiplayerAttackOption.LEFT, RangeOfInfluence.ALL,
                    MulliganType.GAME_DEFAULT.getMulligan(0), 60, 20, 7);
        }

        @Override
        public TwoPlayerDuel copy() {
            copyThreads.add(Thread.currentThread().getName());
            return super.copy();
        }
    }

    /** Runs getGameView on a thread with the given name and returns the built view. */
    private static GameView getGameViewOnThread(String threadName, GameSessionWatcher session) throws Exception {
        AtomicReference<GameView> result = new AtomicReference<>();
        AtomicReference<Throwable> error = new AtomicReference<>();
        Thread thread = new Thread(() -> {
            try {
                result.set(session.getGameView());
            } catch (Throwable t) {
                error.set(t);
            }
        }, threadName);
        thread.start();
        thread.join(TimeUnit.SECONDS.toMillis(30));
        assertThat(thread.isAlive()).as("getGameView on %s timed out", threadName).isFalse();
        assertThat(error.get()).as("getGameView on %s threw", threadName).isNull();
        return result.get();
    }

    /** ManagerFactory stub for GameSessionPlayer: constructor-only dependencies. */
    private static ManagerFactory stubManagerFactory() {
        ThreadExecutor threadExecutor = (ThreadExecutor) Proxy.newProxyInstance(
                GameSessionWatcherThreadingTest.class.getClassLoader(), new Class<?>[]{ThreadExecutor.class},
                (proxy, method, args) -> {
                    if (method.getName().equals("getCallExecutor")) {
                        return null; // stored but unused by getGameView
                    }
                    throw new UnsupportedOperationException(method.getName());
                });
        return (ManagerFactory) Proxy.newProxyInstance(
                GameSessionWatcherThreadingTest.class.getClassLoader(), new Class<?>[]{ManagerFactory.class},
                (proxy, method, args) -> {
                    switch (method.getName()) {
                        case "userManager":
                            return null; // stored but unused by getGameView
                        case "threadExecutor":
                            return threadExecutor;
                        default:
                            throw new UnsupportedOperationException(method.getName());
                    }
                });
    }

    private final CopyRecordingDuel liveGame = new CopyRecordingDuel();
    private final AtomicReference<GameSnapshot> lastStableGame;
    private final int snapshotSeq;

    public GameSessionWatcherThreadingTest() {
        // set before GameController snapshots the game in the real flow (TableController.startGame)
        liveGame.setGameOptions(new mage.game.GameOptions());
        liveGame.getState().setTurnNum(3);
        lastStableGame = new AtomicReference<>(GameSnapshot.of(liveGame.copy()));
        snapshotSeq = lastStableGame.get().gameSeq();
        // live game moves on after the snapshot was published
        liveGame.getState().setTurnNum(7);
        liveGame.nextGameSeq();
        liveGame.copyThreads.clear();
    }

    @Test
    @DisplayName("off the game thread, the watcher view is built from the snapshot without copying the live game")
    void offGameThreadUsesSnapshot() throws Exception {
        GameSessionWatcher watcher = new GameSessionWatcher(null, UUID.randomUUID(), liveGame, lastStableGame, false);
        GameSnapshot snapshotBefore = lastStableGame.get();

        GameView view = getGameViewOnThread(ThreadUtils.THREAD_PREFIX_CALL_REQUEST + "-test", watcher);

        assertThat(view.getTurn()).as("view must reflect the snapshot, not the live game").isEqualTo(3);
        assertThat(view.getGameSeq())
                .as("view must report the seq captured at snapshot publish, not the live counter")
                .isEqualTo(snapshotSeq);
        assertThat(liveGame.copyThreads).as("live game must not be copied off the game thread").isEmpty();
        assertThat(lastStableGame.get()).as("off-thread builds must not republish the snapshot").isSameAs(snapshotBefore);
    }

    @Test
    @DisplayName("on the game thread, the watcher view is built from a live copy which becomes the new snapshot")
    void onGameThreadCopiesLiveGameAndPublishesSnapshot() throws Exception {
        GameSessionWatcher watcher = new GameSessionWatcher(null, UUID.randomUUID(), liveGame, lastStableGame, false);
        GameSnapshot snapshotBefore = lastStableGame.get();

        GameView view = getGameViewOnThread(ThreadUtils.THREAD_PREFIX_GAME + " test", watcher);

        assertThat(view.getTurn()).as("view must reflect the live game").isEqualTo(7);
        assertThat(view.getGameSeq()).isEqualTo(liveGame.getGameSeq());
        assertThat(liveGame.copyThreads).hasSize(1);
        assertThat(lastStableGame.get()).as("game-thread builds must republish the snapshot").isNotSameAs(snapshotBefore);
        assertThat(lastStableGame.get().gameSeq()).isEqualTo(liveGame.getGameSeq());

        // a later off-thread build now sees the republished state and seq
        GameView followUp = getGameViewOnThread(ThreadUtils.THREAD_PREFIX_CALL_REQUEST + "-test-2", watcher);
        assertThat(followUp.getTurn()).isEqualTo(7);
        assertThat(followUp.getGameSeq()).isEqualTo(liveGame.getGameSeq());
    }

    @Test
    @DisplayName("the player session view (getGameView RPC) follows the same thread rules")
    void playerSessionUsesSnapshotOffGameThread() throws Exception {
        GameSessionPlayer player = new GameSessionPlayer(stubManagerFactory(), liveGame, UUID.randomUUID(), UUID.randomUUID(), lastStableGame);

        GameView offThreadView = getGameViewOnThread(ThreadUtils.THREAD_PREFIX_CALL_REQUEST + "-test", player);
        assertThat(offThreadView.getTurn()).isEqualTo(3);
        assertThat(offThreadView.getGameSeq()).isEqualTo(snapshotSeq);
        assertThat(liveGame.copyThreads).as("live game must not be copied off the game thread").isEmpty();

        GameView onThreadView = getGameViewOnThread(ThreadUtils.THREAD_PREFIX_GAME + " test", player);
        assertThat(onThreadView.getTurn()).isEqualTo(7);
        assertThat(liveGame.copyThreads).hasSize(1);
        assertThat(lastStableGame.get().game().getState().getTurnNum()).isEqualTo(7);
    }
}
