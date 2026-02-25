package mage.client.observer;

import mage.view.GameView;
import mage.view.PlayerView;

import java.util.HashSet;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;

/**
 * Tracks the actual game round number by observing which players have taken turns.
 * XMage's turnNum increments per-player-turn, so in a 4-player game "turn 5" is really
 * round 2. This tracker handles extra turns and player elimination correctly.
 */
public class RoundTracker {
    private int gameRound = 1;
    private int lastTurnNum = 0;
    private final Set<UUID> turnsTakenThisRound = new HashSet<>();

    /**
     * Update tracking with the latest game state and return the current round number.
     */
    public int update(GameView game) {
        int turnNum = game.getTurn();
        if (turnNum > lastTurnNum) {
            lastTurnNum = turnNum;
            UUID activeId = game.getActivePlayerId();
            if (activeId != null) {
                turnsTakenThisRound.add(activeId);
            }
            // Check if all living players have taken a turn this round
            Set<UUID> living = game.getPlayers().stream()
                    .filter(p -> !p.hasLeft() && p.getLife() > 0)
                    .map(PlayerView::getPlayerId)
                    .collect(Collectors.toSet());
            if (!living.isEmpty() && turnsTakenThisRound.containsAll(living)) {
                gameRound++;
                turnsTakenThisRound.clear();
            }
        }
        return gameRound;
    }

    public int getGameRound() {
        return gameRound;
    }
}
