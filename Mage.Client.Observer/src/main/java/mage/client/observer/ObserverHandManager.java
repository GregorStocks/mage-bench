package mage.client.observer;

import mage.abilities.icon.CardIconRenderSettings;
import mage.cards.Card;
import mage.cards.MageCard;
import mage.client.SessionHandler;
import mage.client.cards.BigCard;
import mage.client.cards.Cards;
import mage.client.dialog.PreferencesDialog;
import mage.client.game.HandPanel;
import mage.client.game.PlayAreaPanel;
import mage.client.plugins.adapters.MageActionCallback;
import mage.client.plugins.impl.Plugins;
import mage.client.util.CardsViewUtil;
import mage.client.util.GUISizeHelper;
import mage.constants.PlayerAction;
import mage.constants.Zone;
import mage.view.CardView;
import mage.view.CardsView;
import mage.view.GameView;
import mage.view.PlayerView;
import mage.view.SimpleCardsView;
import org.apache.log4j.Logger;

import javax.swing.*;
import java.awt.*;
import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

final class ObserverHandManager {

    private static final Logger logger = Logger.getLogger(ObserverHandManager.class);

    private final Set<UUID> permissionsRequested = new HashSet<>();
    private final Map<UUID, Set<UUID>> lastHandCardIds = new HashMap<>();
    private boolean handPanelsInitialized = false;
    private UUID observerGameId;

    void resetForGame(UUID observerGameId) {
        permissionsRequested.clear();
        this.observerGameId = observerGameId;
    }

    void requestHandPermissions(GameView game) {
        if (game == null || game.getPlayers() == null || observerGameId == null) {
            return;
        }

        for (PlayerView player : game.getPlayers()) {
            UUID playerId = player.getPlayerId();
            if (!permissionsRequested.contains(playerId)) {
                permissionsRequested.add(playerId);
                logger.info("Requesting hand permission from player: " + player.getName());
                SessionHandler.sendPlayerAction(
                        PlayerAction.REQUEST_PERMISSION_TO_SEE_HAND_CARDS,
                        observerGameId,
                        playerId
                );
            }
        }
    }

    void distributeHands(
            GameView game,
            Map<UUID, PlayAreaPanel> players,
            Map<String, Card> loadedCards,
            BigCard bigCard,
            UUID gameId
    ) {
        if (game == null || game.getPlayers() == null) {
            return;
        }

        for (PlayerView player : game.getPlayers()) {
            UUID playerId = player.getPlayerId();
            PlayAreaPanel playArea = players.get(playerId);
            if (playArea == null) {
                continue;
            }

            HandPanel handPanel = playArea.getHandPanel();
            if (handPanel == null) {
                continue;
            }

            if (!handPanelsInitialized) {
                handPanel.setScaleToFit(true);
            }

            CardsView currentHand = getHandCardsForPlayer(player, game, loadedCards);
            Set<UUID> currentIds = currentHand != null ? currentHand.keySet() : Set.of();
            Set<UUID> previousIds = lastHandCardIds.getOrDefault(playerId, Set.of());

            if (currentIds.equals(previousIds)) {
                continue;
            }

            if (previousIds.isEmpty()) {
                if (currentHand != null && !currentHand.isEmpty()) {
                    handPanel.loadCards(currentHand, bigCard, gameId);
                    handPanel.setVisible(true);
                } else {
                    handPanel.setVisible(false);
                }
            } else {
                updateHandIncrementally(handPanel, currentHand, previousIds, currentIds, bigCard, gameId);
            }

            lastHandCardIds.put(playerId, new HashSet<>(currentIds));
        }

        handPanelsInitialized = true;
    }

    static CardsView getHandCardsForPlayer(PlayerView player, GameView game, Map<String, Card> loadedCards) {
        String playerName = player.getName();
        Map<String, SimpleCardsView> watchedHands = game.getWatchedHands();
        if (watchedHands != null && watchedHands.containsKey(playerName)) {
            return CardsViewUtil.convertSimple(watchedHands.get(playerName), loadedCards);
        }
        return null;
    }

    private void updateHandIncrementally(
            HandPanel handPanel,
            CardsView currentHand,
            Set<UUID> previousIds,
            Set<UUID> currentIds,
            BigCard bigCard,
            UUID gameId
    ) {
        try {
            Field handField = HandPanel.class.getDeclaredField("hand");
            handField.setAccessible(true);
            Cards hand = (Cards) handField.get(handPanel);

            Field cardAreaField = Cards.class.getDeclaredField("cardArea");
            cardAreaField.setAccessible(true);
            JPanel cardArea = (JPanel) cardAreaField.get(hand);

            Field scrollPaneField = HandPanel.class.getDeclaredField("jScrollPane1");
            scrollPaneField.setAccessible(true);
            JScrollPane scrollPane = (JScrollPane) scrollPaneField.get(handPanel);

            Map<UUID, MageCard> cardsMap = hand.getMageCardsForUpdate();

            var toRemove = new HashSet<>(previousIds);
            toRemove.removeAll(currentIds);

            var toAdd = new HashSet<>(currentIds);
            toAdd.removeAll(previousIds);

            boolean changed = !toRemove.isEmpty() || !toAdd.isEmpty();

            if (!changed) {
                return;
            }

            cardArea.setVisible(false);

            int newCardCount = currentIds.size();
            Dimension newDimension = calculateScaledCardDimension(scrollPane, newCardCount);

            for (UUID cardId : toRemove) {
                MageCard card = cardsMap.remove(cardId);
                if (card != null) {
                    cardArea.remove(card);
                }
            }

            if (currentHand != null) {
                for (UUID cardId : toAdd) {
                    CardView cardView = currentHand.get(cardId);
                    if (cardView != null) {
                        addCardToHandWithDimension(hand, cardArea, cardsMap, cardView, newDimension, bigCard, gameId);
                    }
                }
            }

            for (MageCard card : cardsMap.values()) {
                card.setCardBounds(0, 0, newDimension.width, newDimension.height);
            }

            layoutHandCards(cardArea, Zone.HAND);
            hand.sizeCards(newDimension);
            cardArea.setVisible(true);

            handPanel.setVisible(!cardsMap.isEmpty());
        } catch (NoSuchFieldException | IllegalAccessException e) {
            logger.warn("Failed to update hand incrementally, falling back to full load", e);
            if (currentHand != null && !currentHand.isEmpty()) {
                handPanel.loadCards(currentHand, bigCard, gameId);
                handPanel.setVisible(true);
            } else {
                handPanel.setVisible(false);
            }
        }
    }

    private Dimension calculateScaledCardDimension(JScrollPane scrollPane, int cardCount) {
        if (cardCount == 0) {
            return GUISizeHelper.handCardDimension;
        }

        int availableWidth = scrollPane.getViewport().getWidth();
        if (availableWidth <= 0) {
            return GUISizeHelper.handCardDimension;
        }

        int gapX = MageActionCallback.HAND_CARDS_BETWEEN_GAP_X;
        int totalMargins = MageActionCallback.HAND_CARDS_MARGINS.getLeft()
                + MageActionCallback.HAND_CARDS_MARGINS.getRight();
        int totalGaps = (cardCount - 1) * gapX;
        int widthForCards = availableWidth - totalMargins - totalGaps;

        int cardWidth = widthForCards / cardCount;

        int baseWidth = GUISizeHelper.handCardDimension.width;
        int minWidth = baseWidth / 3;
        cardWidth = Math.min(cardWidth, baseWidth);
        cardWidth = Math.max(cardWidth, minWidth);

        int cardHeight = (int) (cardWidth * GUISizeHelper.CARD_WIDTH_TO_HEIGHT_COEF);

        return new Dimension(cardWidth, cardHeight);
    }

    private void addCardToHandWithDimension(
            Cards hand,
            JPanel cardArea,
            Map<UUID, MageCard> cardsMap,
            CardView cardView,
            Dimension cardDimension,
            BigCard bigCard,
            UUID gameId
    ) {
        MageCard mageCard = Plugins.instance.getMageCard(
                cardView,
                bigCard,
                new CardIconRenderSettings(),
                cardDimension,
                gameId,
                true,
                true,
                PreferencesDialog.getRenderMode(),
                true
        );

        mageCard.setCardContainerRef(cardArea);
        mageCard.update(cardView);
        mageCard.setZone(Zone.HAND);
        mageCard.setCardBounds(0, 0, cardDimension.width, cardDimension.height);

        cardsMap.put(cardView.getId(), mageCard);
        cardArea.add(mageCard);

        int dx = MageActionCallback.getHandOrStackMargins(Zone.HAND).getLeft();
        for (Component comp : cardArea.getComponents()) {
            if (comp instanceof MageCard existing && existing != mageCard) {
                dx = Math.max(dx, existing.getCardLocation().getCardX()
                        + existing.getCardLocation().getCardWidth()
                        + MageActionCallback.getHandOrStackBetweenGapX(Zone.HAND));
            }
        }
        mageCard.setCardLocation(dx, MageActionCallback.getHandOrStackMargins(Zone.HAND).getTop());
    }

    private void layoutHandCards(JPanel cardArea, Zone zone) {
        var cardsToLayout = new ArrayList<MageCard>();
        for (Component component : cardArea.getComponents()) {
            if (component instanceof MageCard mc) {
                cardsToLayout.add(mc);
            }
        }

        cardsToLayout.sort(Comparator.comparingInt(cp -> cp.getCardLocation().getCardX()));

        int dx = MageActionCallback.getHandOrStackBetweenGapX(zone);
        for (MageCard card : cardsToLayout) {
            card.setCardLocation(dx, card.getCardLocation().getCardY());
            dx += card.getCardLocation().getCardWidth() + MageActionCallback.getHandOrStackBetweenGapX(zone);
        }
    }
}
