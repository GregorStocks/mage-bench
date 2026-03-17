package mage.client.observer;

import mage.constants.Zone;

import java.awt.*;

/**
 * observer-mode exile panel with wider cards, a zone label, and a
 * semi-transparent reddish tint over cards to visually distinguish
 * exile from graveyard.  Fixed size — cards compress to always fit.
 */
public class ObserverExilePanel extends StackedObserverZonePanel {

    // Red tint painted over exile cards for visual differentiation
    private static final Color EXILE_TINT = new Color(120, 30, 30, 40);
    private static final Color LABEL_COLOR = new Color(200, 130, 130);
    private static final Color BACKGROUND_COLOR = new Color(90, 40, 40);

    public ObserverExilePanel() {
        this(80);
    }

    public ObserverExilePanel(int cardWidth) {
        this(cardWidth, 2);
    }

    public ObserverExilePanel(int cardWidth, int contentHeightMultiplier) {
        super(cardWidth, contentHeightMultiplier, Zone.EXILED, "EXILE", LABEL_COLOR, BACKGROUND_COLOR, EXILE_TINT);
    }
}
