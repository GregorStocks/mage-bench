package mage.server.game;

import mage.game.BridgeLogEntry;
import mage.game.Game;
import mage.game.Table;
import mage.interfaces.callback.ClientCallback;
import mage.interfaces.callback.ClientCallbackMethod;
import mage.players.Player;
import mage.server.User;
import mage.server.managers.UserManager;
import mage.util.ThreadUtils;
import mage.view.GameClientMessage;
import mage.view.GameEndView;
import mage.view.GameView;
import mage.view.SimpleCardsView;
import org.apache.log4j.Logger;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Function;

/**
 * @author BetaSteward_at_googlemail.com
 */
public class GameSessionWatcher {

    protected static final Logger logger = Logger.getLogger(GameSessionWatcher.class);

    private final UserManager userManager;
    protected final UUID userId;
    protected final Game game;
    // Last game copy published by a game-thread view build (owned by GameController,
    // shared by all sessions of the game). Never null: seeded before the game starts.
    private final AtomicReference<Game> lastStableGame;
    protected boolean killed = false;
    protected final boolean isPlayer;
    private int bridgeEventCursor = 0;

    public GameSessionWatcher(UserManager userManager, UUID userId, Game game, AtomicReference<Game> lastStableGame, boolean isPlayer) {
        this.userManager = userManager;
        this.userId = userId;
        this.game = game;
        this.lastStableGame = lastStableGame;
        this.isPlayer = isPlayer;
    }

    public boolean init() {
        if (!killed) {
            Optional<User> user = userManager.getUser(userId);
            if (user.isPresent()) {
                // can be called outside of the game thread, e.g. user starts watching an already
                // running game — getGameView handles that by building from the last stable snapshot
                long startNanos = System.nanoTime();
                long viewStartNanos = startNanos;
                boolean onGameThread = ThreadUtils.isRunGameThread();
                logger.info(String.format(
                        "Watcher init start: game=%s, userId=%s, isPlayer=%s, onGameThread=%s, turn=%s, step=%s, thread=%s",
                        game.getId(),
                        userId,
                        isPlayer,
                        onGameThread,
                        game.getTurnNum(),
                        game.getTurnStepType(),
                        Thread.currentThread().getName()
                ));
                GameView gameView = getGameView();
                long viewMs = (System.nanoTime() - viewStartNanos) / 1_000_000;
                user.get().fireCallback(new ClientCallback(ClientCallbackMethod.GAME_INIT, game.getId(), gameView));
                long totalMs = (System.nanoTime() - startNanos) / 1_000_000;
                logger.info(String.format(
                        "Watcher init complete: game=%s, userId=%s, isPlayer=%s, onGameThread=%s, viewMs=%d, totalMs=%d, turn=%s, step=%s, thread=%s",
                        game.getId(),
                        userId,
                        isPlayer,
                        onGameThread,
                        viewMs,
                        totalMs,
                        game.getTurnNum(),
                        game.getTurnStepType(),
                        Thread.currentThread().getName()
                ));
                return true;
            }
        }
        return false;
    }

    public void update() {
        if (!killed) {
            userManager.getUser(userId).ifPresent(user -> user.fireCallback(
                gameCallbackWithBridgeEvents(ClientCallbackMethod.GAME_UPDATE, getGameView())));
        }

    }

    public void inform(final String message) {
        if (!killed) {
            userManager.getUser(userId).ifPresent(user -> user.fireCallback(
                gameCallbackWithBridgeEvents(ClientCallbackMethod.GAME_UPDATE_AND_INFORM,
                    new GameClientMessage(getGameView(), null, message))));
        }

    }

    public void informPersonal(final String message) {
        if (!killed) {
            userManager.getUser(userId).ifPresent(user -> user.fireCallback(new ClientCallback(ClientCallbackMethod.GAME_INFORM_PERSONAL, game.getId(), new GameClientMessage(getGameView(), null, message))));
        }

    }

    public void gameOver(final String message) {
        if (!killed) {
            userManager.getUser(userId).ifPresent(user -> {
                user.removeGameWatchInfo(game.getId());
                logger.info("Sending GAME_OVER to user " + user.getName() + " (userId=" + userId + ") for game " + game.getId());
                user.fireCallback(new ClientCallback(ClientCallbackMethod.GAME_OVER, game.getId(), new GameClientMessage(getGameView(), null, message)));
            });
            if (!userManager.getUser(userId).isPresent()) {
                logger.warn("GAME_OVER not sent - user not found for userId=" + userId + ", game=" + game.getId());
            }
        } else {
            logger.warn("GAME_OVER not sent - session killed for userId=" + userId + ", game=" + game.getId());
        }
    }

    /**
     * Cleanup if Session ends
     */
    public void cleanUp() {

    }

    public void gameError(final String message) {
        if (!killed) {
            userManager.getUser(userId).ifPresent(user -> user.fireCallback(new ClientCallback(ClientCallbackMethod.GAME_ERROR, game.getId(), message)));
        }
    }

    public void setKilled() {
        killed = true;
    }

    public GameView getGameView() {
        long startNanos = System.nanoTime();
        boolean onGameThread = ThreadUtils.isRunGameThread();

        GameView gameView = buildGameView(sourceGame -> {
            GameView view = new GameView(sourceGame.getState(), sourceGame, null, userId);
            processWatchedHands(sourceGame, userId, view);
            view.assignShortIdsToHands();
            return view;
        });

        long elapsedMs = (System.nanoTime() - startNanos) / 1_000_000;
        if (!onGameThread || (!isPlayer && elapsedMs >= 250)) {
            logger.info(String.format(
                    "Game view built: game=%s, userId=%s, isPlayer=%s, onGameThread=%s, elapsedMs=%d, turn=%s, step=%s, thread=%s",
                    game.getId(),
                    userId,
                    isPlayer,
                    onGameThread,
                    elapsedMs,
                    game.getTurnNum(),
                    game.getTurnStepType(),
                    Thread.currentThread().getName()
            ));
        }

        return gameView;
    }

    /**
     * Runs viewBuilder against a game copy that is safe to read on the current thread.
     *
     * Copying the live game is only safe on the game thread: an RPC thread copying a game
     * the game thread is concurrently mutating can throw ConcurrentModificationException or
     * produce a corrupt view (issue watcher-getgameview-off-thread-copy). Off the game
     * thread, copy the last stable snapshot published by a game-thread build instead.
     * Snapshots are quiescent once published — nothing mutates them after the reference is
     * set, so copying one off-thread is safe.
     */
    protected GameView buildGameView(Function<Game, GameView> viewBuilder) {
        boolean onGameThread = ThreadUtils.isRunGameThread();
        Game sourceGame = onGameThread ? game.copy() : lastStableGame.get().copy();
        GameView gameView = viewBuilder.apply(sourceGame);
        if (onGameThread) {
            // publish only after the view is built, so the snapshot is never mutated again
            lastStableGame.set(sourceGame);
        }
        return gameView;
    }

    protected static void processWatchedHands(Game game, UUID userId, GameView gameView) {
        gameView.getWatchedHands().clear();
        for (Player player : game.getPlayers().values()) {
            if (player.hasUserPermissionToSeeHand(userId)) {
                gameView.getWatchedHands().put(player.getName(), new SimpleCardsView(player.getHand().getCards(game), true));
            }
        }
    }

    public GameEndView getGameEndView(UUID playerId, Table table) {
        return new GameEndView(game.getState(), game, playerId, table);
    }

    public boolean isPlayer() {
        return isPlayer;
    }

    protected ClientCallback gameCallbackWithBridgeEvents(ClientCallbackMethod method, Object data) {
        ClientCallback callback = new ClientCallback(method, game.getId(), data);
        callback.setBridgeEvents(fetchAndAdvanceBridgeEvents());
        return callback;
    }

    private List<BridgeLogEntry> fetchAndAdvanceBridgeEvents() {
        List<BridgeLogEntry> events = game.getBridgeEventsSince(bridgeEventCursor, getPlayerIdForBridgeEvents());
        if (!events.isEmpty()) {
            bridgeEventCursor = events.get(events.size() - 1).index() + 1;
        }
        return events;
    }

    protected UUID getPlayerIdForBridgeEvents() {
        return null;
    }

}
