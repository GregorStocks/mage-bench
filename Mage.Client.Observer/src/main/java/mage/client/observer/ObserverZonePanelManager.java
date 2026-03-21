package mage.client.observer;

import mage.cards.Card;
import mage.client.cards.BigCard;
import mage.client.game.ExilePanel;
import mage.client.game.GraveyardPanel;
import mage.client.game.PlayAreaPanel;
import mage.client.game.PlayerPanelExt;
import mage.client.util.ImageHelper;
import mage.view.CardsView;
import mage.view.CommandObjectView;
import mage.view.CommanderView;
import mage.view.GameView;
import mage.view.PlayerView;
import org.apache.log4j.Logger;
import org.mage.plugins.card.images.ImageCache;
import org.mage.plugins.card.images.ImageCacheData;

import javax.swing.*;
import java.awt.*;
import java.awt.image.BufferedImage;
import java.lang.reflect.Field;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

final class ObserverZonePanelManager {

    private static final Logger logger = Logger.getLogger(ObserverZonePanelManager.class);

    private final Map<UUID, CommanderPanel> commanderPanels = new HashMap<>();
    private final Map<UUID, ObserverGraveyardPanel> observerGraveyardPanels = new HashMap<>();
    private final Map<UUID, ObserverExilePanel> observerExilePanels = new HashMap<>();
    private final Map<UUID, UUID> playerCommanderAvatars = new HashMap<>();
    private boolean zonePanelsInjected = false;

    void distributeGraveyards(GameView game, BigCard bigCard, UUID gameId) {
        if (game == null || game.getPlayers() == null) {
            return;
        }

        for (PlayerView player : game.getPlayers()) {
            ObserverGraveyardPanel panel = observerGraveyardPanels.get(player.getPlayerId());
            if (panel == null) {
                continue;
            }

            CardsView graveyardCards = player.getGraveyard();
            if (graveyardCards != null) {
                panel.loadCards(graveyardCards, bigCard, gameId);
            }
        }
    }

    void distributeExile(GameView game, BigCard bigCard, UUID gameId) {
        if (game == null || game.getPlayers() == null) {
            return;
        }

        for (PlayerView player : game.getPlayers()) {
            ObserverExilePanel panel = observerExilePanels.get(player.getPlayerId());
            if (panel == null) {
                continue;
            }

            CardsView exileCards = player.getExile();
            if (exileCards != null) {
                if (!exileCards.isEmpty()) {
                    logger.info("Player " + player.getName() + " has " + exileCards.size() + " exiled cards");
                }
                panel.loadCards(exileCards, bigCard, gameId);
            }
        }
    }

    void injectZonePanels(GameView game, Map<UUID, PlayAreaPanel> players) {
        if (zonePanelsInjected || game == null || game.getPlayers() == null) {
            return;
        }

        boolean hasCommanders = hasCommanderViews(game);

        for (PlayerView player : game.getPlayers()) {
            PlayAreaPanel playArea = players.get(player.getPlayerId());
            if (playArea == null) {
                continue;
            }

            try {
                PlayerPanelExt playerPanel = playArea.getPlayerPanel();
                if (playerPanel == null || playerPanel.getParent() == null) {
                    continue;
                }

                Container westPanel = playerPanel.getParent();
                if (!(westPanel instanceof JPanel)) {
                    continue;
                }

                UUID playerId = player.getPlayerId();

                GraveyardPanel oldGy = playArea.getGraveyardPanel();
                if (oldGy != null) {
                    westPanel.remove(oldGy);
                }
                ExilePanel oldEx = playArea.getExilePanel();
                if (oldEx != null) {
                    westPanel.remove(oldEx);
                }

                int zoneCardWidth = (int) (80 * ObserverUiScale.computeScaleFactor(playArea));
                int nextIndex = 1;

                if (hasCommanders) {
                    var commanderPanel = new CommanderPanel(zoneCardWidth);
                    commanderPanels.put(playerId, commanderPanel);
                    westPanel.add(commanderPanel, nextIndex++);
                }

                var graveyardPanel = new ObserverGraveyardPanel(zoneCardWidth);
                observerGraveyardPanels.put(playerId, graveyardPanel);

                int exileHeightMultiplier = hasCommanders ? 2 : 3;
                var exilePanel = new ObserverExilePanel(zoneCardWidth, exileHeightMultiplier);
                observerExilePanels.put(playerId, exilePanel);

                westPanel.add(graveyardPanel, nextIndex++);
                westPanel.add(exilePanel, nextIndex);

                westPanel.revalidate();
                westPanel.repaint();

                logger.info("Injected zone panels for player: " + player.getName());
            } catch (Exception e) {
                logger.warn("Failed to inject zone panels for player: " + player.getName(), e);
            }
        }

        zonePanelsInjected = true;
    }

    void distributeCommanders(GameView game, BigCard bigCard, UUID gameId) {
        if (game == null || game.getPlayers() == null) {
            return;
        }

        for (PlayerView player : game.getPlayers()) {
            CommanderPanel panel = commanderPanels.get(player.getPlayerId());
            if (panel == null) {
                continue;
            }

            java.util.List<CommandObjectView> cmdList = player.getCommandObjectList();
            logger.info("Player " + player.getName() + " command list size: " + cmdList.size());
            for (CommandObjectView obj : cmdList) {
                logger.info("  - " + obj.getClass().getSimpleName() + ": " + obj.getName() + " (id: " + obj.getId() + ")");
            }

            var commanders = new CardsView();
            for (CommandObjectView obj : player.getCommandObjectList()) {
                if (obj instanceof CommanderView cv) {
                    commanders.put(obj.getId(), cv);
                }
            }

            logger.info("Player " + player.getName() + " commanders found: " + commanders.size());
            panel.loadCards(commanders, bigCard, gameId);
        }
    }

    void replaceAvatarsWithCommanderArt(GameView game, Map<UUID, PlayAreaPanel> players) {
        if (game == null || game.getPlayers() == null) {
            return;
        }

        for (PlayerView player : game.getPlayers()) {
            UUID playerId = player.getPlayerId();
            PlayAreaPanel playArea = players.get(playerId);
            if (playArea == null) {
                continue;
            }

            CommanderView commander = null;
            for (CommandObjectView obj : player.getCommandObjectList()) {
                if (obj instanceof CommanderView cv) {
                    commander = cv;
                    break;
                }
            }

            if (commander == null) {
                continue;
            }

            UUID commanderId = commander.getId();
            if (commanderId.equals(playerCommanderAvatars.get(playerId))) {
                continue;
            }

            ImageCacheData cacheData = ImageCache.getCardImageOriginal(commander);
            BufferedImage cardImage = cacheData != null ? cacheData.getImage() : null;

            if (cardImage == null) {
                continue;
            }

            BufferedImage artCrop = cropCardArt(cardImage);
            int avatarSize = ObserverUiScale.computeAvatarSize(playArea);
            var avatarRect = new Rectangle(avatarSize, avatarSize);
            BufferedImage avatarImage = ImageHelper.getResizedImage(artCrop, avatarRect);

            try {
                PlayerPanelExt playerPanel = playArea.getPlayerPanel();
                Field avatarField = PlayerPanelExt.class.getDeclaredField("avatar");
                avatarField.setAccessible(true);
                mage.client.components.HoverButton avatar =
                        (mage.client.components.HoverButton) avatarField.get(playerPanel);

                if (avatar != null) {
                    avatar.update(
                            player.getName(),
                            avatarImage,
                            avatarImage,
                            avatarImage,
                            avatarImage,
                            avatarRect
                    );
                    avatar.repaint();
                }

                playerCommanderAvatars.put(playerId, commanderId);
                logger.info("Replaced avatar for " + player.getName()
                        + " with commander art: " + commander.getName());

            } catch (NoSuchFieldException | IllegalAccessException e) {
                logger.warn("Failed to replace avatar for " + player.getName(), e);
            }
        }
    }

    private static boolean hasCommanderViews(GameView game) {
        for (PlayerView player : game.getPlayers()) {
            for (CommandObjectView obj : player.getCommandObjectList()) {
                if (obj instanceof CommanderView) {
                    return true;
                }
            }
        }
        return false;
    }

    private static BufferedImage cropCardArt(BufferedImage cardImage) {
        int cardW = cardImage.getWidth();
        int cardH = cardImage.getHeight();

        int artX = (int) (cardW * 0.08);
        int artY = (int) (cardH * 0.12);
        int artW = (int) (cardW * 0.84);
        int artH = (int) (cardH * 0.37);

        artX = Math.max(0, Math.min(artX, cardW - 1));
        artY = Math.max(0, Math.min(artY, cardH - 1));
        artW = Math.min(artW, cardW - artX);
        artH = Math.min(artH, cardH - artY);

        int squareSize = Math.min(artW, artH);
        int squareX = artX + (artW - squareSize) / 2;
        int squareY = artY + (artH - squareSize) / 2;

        if (squareSize <= 0) {
            squareSize = Math.min(cardW, cardH) / 2;
            squareX = (cardW - squareSize) / 2;
            squareY = (cardH - squareSize) / 2;
        }

        return cardImage.getSubimage(squareX, squareY, squareSize, squareSize);
    }
}
