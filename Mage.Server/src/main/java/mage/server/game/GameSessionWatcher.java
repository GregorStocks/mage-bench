package mage.server.game;

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
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/**
 * @author BetaSteward_at_googlemail.com
 */
public class GameSessionWatcher {

    protected static final Logger logger = Logger.getLogger(GameSessionWatcher.class);

    private final UserManager userManager;
    protected final UUID userId;
    protected final Game game;
    protected boolean killed = false;
    protected final boolean isPlayer;

    public GameSessionWatcher(UserManager userManager, UUID userId, Game game, boolean isPlayer) {
        this.userManager = userManager;
        this.userId = userId;
        this.game = game;
        this.isPlayer = isPlayer;
    }

    public boolean init() {
        if (!killed) {
            Optional<User> user = userManager.getUser(userId);
            if (user.isPresent()) {
                // TODO: can be called outside of the game thread, e.g. user start watching already running game
                //    possible fix: getGameView must use last cached value in non game thread call (split by sessions)
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
            userManager.getUser(userId).ifPresent(user -> user.fireCallback(new ClientCallback(ClientCallbackMethod.GAME_UPDATE, game.getId(), getGameView())));
        }

    }

    public void inform(final String message) {
        if (!killed) {
            userManager.getUser(userId).ifPresent(user -> user.fireCallback(new ClientCallback(ClientCallbackMethod.GAME_UPDATE_AND_INFORM, game.getId(), new GameClientMessage(getGameView(), null, message))));
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

        // game view calculation can take some time and can be called from non-game thread,
        // so use copy for thread save (protection from ConcurrentModificationException)
        Game sourceGame = game.copy();

        GameView gameView = new GameView(sourceGame.getState(), sourceGame, null, userId);
        processWatchedHands(sourceGame, userId, gameView);
        gameView.assignShortIdsToHands();

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

}
