package mage.client.observer;

import mage.cards.MageCard;
import mage.client.cards.BigCard;
import mage.constants.Zone;
import mage.view.CardsView;
import org.apache.log4j.Logger;

import java.awt.*;
import java.util.UUID;
import javax.swing.*;

/**
 * Panel for displaying commander cards in the observer/spectator west panel.
 * Always visible (shows empty placeholder when no commanders loaded).
 * Supports partner commanders displayed side-by-side.
 */
public class CommanderPanel extends ObserverZonePanel {

    private static final Logger logger = Logger.getLogger(CommanderPanel.class);

    private static final Color LABEL_COLOR = new Color(200, 180, 120);
    private static final Color BACKGROUND_COLOR = new Color(100, 80, 40);

    public CommanderPanel() {
        this(80);
    }

    public CommanderPanel(int cardWidth) {
        super(cardWidth, 1, 4, Zone.COMMAND, "CMD", LABEL_COLOR, BACKGROUND_COLOR, null);
    }

    @Override
    public void loadCards(CardsView cardsView, BigCard bigCard, UUID gameId) {
        logger.info("CommanderPanel.loadCards called with " + (cardsView != null ? cardsView.size() : "null") + " cards");
        super.loadCards(cardsView, bigCard, gameId);
        logger.info("CommanderPanel after load: " + getCardCount() + " cards");
    }

    @Override
    protected void layoutCards() {
        int x = 2; // Small left margin
        for (MageCard card : getCardPanels().values()) {
            card.setCardBounds(x, 2, getCardWidthPixels(), getCardHeightPixels());
            x += getCardWidthPixels() + getCardGapPixels();
        }
        getCardAreaPanel().setPreferredSize(new Dimension(getPanelWidthPixels(), getContentHeightPixels()));
    }
}
