package mage.client.observer;

import mage.cards.MageCard;
import mage.constants.Zone;

import java.awt.*;
import java.util.ArrayList;
import java.util.List;

abstract class StackedObserverZonePanel extends ObserverZonePanel {

    private final int maxStackOffset;
    private final int minStackOffset;

    protected StackedObserverZonePanel(
            int cardWidth,
            int contentHeightMultiplier,
            Zone zone,
            String labelText,
            Color labelColor,
            Color backgroundColor,
            Color overlayTint
    ) {
        super(cardWidth, contentHeightMultiplier, 0, zone, labelText, labelColor, backgroundColor, overlayTint);

        double scale = cardWidth / 80.0;
        this.maxStackOffset = Math.max(5, (int) (24 * scale));
        this.minStackOffset = Math.max(3, (int) (5 * scale));
    }

    @Override
    protected final void layoutCards() {
        getCardAreaPanel().setPreferredSize(new Dimension(getPanelWidthPixels(), getContentHeightPixels()));
        if (getCardPanels().isEmpty()) {
            return;
        }

        List<MageCard> cardList = new ArrayList<>(getCardPanels().values());
        int offset = 0;
        if (cardList.size() > 1) {
            int availableForOffsets = getContentHeightPixels() - getCardHeightPixels();
            offset = Math.min(maxStackOffset, availableForOffsets / (cardList.size() - 1));
            offset = Math.max(minStackOffset, offset);
        }

        int y = 0;
        for (int i = 0; i < cardList.size(); i++) {
            MageCard card = cardList.get(i);
            card.setCardBounds(0, y, getCardWidthPixels(), getCardHeightPixels());
            getCardAreaPanel().setComponentZOrder(card, cardList.size() - 1 - i);
            if (i < cardList.size() - 1) {
                y += offset;
            }
        }
    }
}
