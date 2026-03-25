package mage.client.bridge;

import mage.util.ShortIdRegistry;
import mage.view.CardView;
import mage.view.CommandObjectView;
import mage.view.CommanderView;
import mage.view.ExileView;
import mage.view.GameView;
import mage.view.LookedAtView;
import mage.view.PermanentView;
import mage.view.PlayerView;
import mage.view.SimpleCardView;

import java.util.Objects;
import java.util.UUID;
import java.util.function.Consumer;

public final class BridgeViewLocator {

    private final ShortIdRegistry shortIds;
    private final Consumer<String> errorLogger;

    public BridgeViewLocator(
            ShortIdRegistry shortIds,
            Consumer<String> errorLogger
    ) {
        this.shortIds = shortIds;
        this.errorLogger = errorLogger;
    }

    public String getStableShortId(UUID objectId, CardView cardView, GameView gameView) {
        Objects.requireNonNull(objectId, "objectId");
        if (cardView != null) {
            String serverShortId = cardView.getShortId();
            if (serverShortId != null && !serverShortId.isBlank()) {
                UUID existing = shortIds.tryResolve(serverShortId);
                if (existing != null && !existing.equals(objectId)) {
                    errorLogger.accept("Server short ID collision: " + serverShortId
                        + " was mapped to " + existing + " but server now says " + objectId);
                }
                shortIds.register(objectId, serverShortId);
                return serverShortId;
            }
        }
        String found = findNonCardViewShortId(objectId, gameView);
        if (found != null) {
            shortIds.register(objectId, found);
            return found;
        }
        return shortIds.getOrAssign(objectId);
    }

    public int getStableShortIdSequence(UUID objectId, CardView cardView, GameView gameView) {
        return parseShortIdSequence(getStableShortId(objectId, cardView, gameView));
    }

    public CardView findCardViewById(UUID objectId, GameView gameView) {
        if (gameView == null) {
            return null;
        }

        CardView found = gameView.getMyHand().get(objectId);
        if (found != null) {
            return found;
        }

        found = gameView.getStack().get(objectId);
        if (found != null) {
            return found;
        }

        for (PlayerView player : gameView.getPlayers()) {
            PermanentView permanent = player.getBattlefield().get(objectId);
            if (permanent != null) {
                return permanent;
            }

            found = player.getGraveyard().get(objectId);
            if (found != null) {
                return found;
            }

            found = player.getExile().get(objectId);
            if (found != null) {
                return found;
            }

            for (CommandObjectView cmd : player.getCommandObjectList()) {
                if (cmd instanceof CommanderView cv && cmd.getId().equals(objectId)) {
                    return cv;
                }
            }
        }

        for (ExileView exileZone : gameView.getExile()) {
            for (CardView card : exileZone.values()) {
                if (card.getId().equals(objectId)) {
                    return card;
                }
            }
        }

        for (CardView card : gameView.getMyHand().values()) {
            CardView secondFace = card.getSecondCardFace();
            if (secondFace != null && secondFace.getId().equals(objectId)) {
                return secondFace;
            }
        }

        return null;
    }

    public PermanentView findPermanentViewById(UUID objectId, GameView gameView) {
        if (gameView == null) {
            return null;
        }
        for (PlayerView player : gameView.getPlayers()) {
            PermanentView perm = player.getBattlefield().get(objectId);
            if (perm != null) {
                return perm;
            }
        }
        return null;
    }

    public static int parseShortIdSequence(String shortId) {
        if (shortId == null || shortId.length() < 2 || (shortId.charAt(0) != 'p' && shortId.charAt(0) != 'l')) {
            return Integer.MAX_VALUE;
        }
        try {
            return Integer.parseInt(shortId.substring(1));
        } catch (NumberFormatException e) {
            return Integer.MAX_VALUE;
        }
    }

    private String findNonCardViewShortId(UUID objectId, GameView gameView) {
        if (gameView == null) {
            return null;
        }
        for (PlayerView pv : gameView.getPlayers()) {
            if (pv.getPlayerId().equals(objectId)) {
                String sid = pv.getShortId();
                if (sid != null && !sid.isBlank()) {
                    return sid;
                }
            }
        }
        for (LookedAtView lv : gameView.getLookedAt()) {
            for (SimpleCardView sv : lv.getCards().values()) {
                if (sv.getId().equals(objectId)) {
                    String sid = sv.getShortId();
                    if (sid != null && !sid.isBlank()) {
                        return sid;
                    }
                }
            }
        }
        return null;
    }
}
