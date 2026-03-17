package mage.client.observer;

import mage.constants.Zone;

import java.awt.*;

/**
 * observer-mode graveyard panel with wider cards and a zone label.
 * Fixed size — cards compress their stack offset to always fit without scrolling.
 */
public class ObserverGraveyardPanel extends StackedObserverZonePanel {

    private static final Color LABEL_COLOR = new Color(140, 140, 180);
    private static final Color BACKGROUND_COLOR = new Color(50, 50, 80);

    public ObserverGraveyardPanel() {
        this(80);
    }

    public ObserverGraveyardPanel(int cardWidth) {
        super(cardWidth, 2, Zone.GRAVEYARD, "GY", LABEL_COLOR, BACKGROUND_COLOR, null);
    }
}
