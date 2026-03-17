package mage.client.observer;

import mage.abilities.icon.CardIconRenderSettings;
import mage.cards.MageCard;
import mage.client.cards.BigCard;
import mage.client.dialog.PreferencesDialog;
import mage.client.plugins.impl.Plugins;
import mage.client.util.GUISizeHelper;
import mage.constants.Zone;
import mage.view.CardView;
import mage.view.CardsView;

import javax.swing.*;
import javax.swing.border.Border;
import javax.swing.border.EmptyBorder;
import java.awt.*;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;

abstract class ObserverZonePanel extends JPanel {

    private static final Border EMPTY_BORDER = new EmptyBorder(0, 0, 0, 0);

    private final int cardWidth;
    private final int cardHeight;
    private final int cardGap;
    private final int contentHeight;
    private final int panelWidth;
    private final Zone zone;
    private final Map<UUID, MageCard> cards = new LinkedHashMap<>();
    private final JPanel cardArea;

    private BigCard bigCard;
    private UUID gameId;

    protected ObserverZonePanel(
            int cardWidth,
            int contentHeightMultiplier,
            int contentHeightPadding,
            Zone zone,
            String labelText,
            Color labelColor,
            Color backgroundColor,
            Color overlayTint
    ) {
        this.cardWidth = cardWidth;
        this.cardHeight = (int) (cardWidth * GUISizeHelper.CARD_WIDTH_TO_HEIGHT_COEF);

        double scale = cardWidth / 80.0;
        this.cardGap = Math.max(3, (int) (5 * scale));
        int margin = Math.max(3, (int) (5 * scale));
        int labelHeight = Math.max(14, (int) (14 * scale));
        this.contentHeight = cardHeight * contentHeightMultiplier + contentHeightPadding;
        this.panelWidth = cardWidth + 2 * margin;
        this.zone = Objects.requireNonNull(zone, "zone must not be null");
        this.cardArea = createCardArea(overlayTint);

        var label = new JLabel(labelText);
        label.setFont(new Font(Font.SANS_SERIF, Font.BOLD, Math.max(10, (int) (10 * scale))));
        label.setForeground(labelColor);
        label.setPreferredSize(new Dimension(0, labelHeight));
        label.setBorder(new EmptyBorder(1, margin, 0, 0));

        setOpaque(true);
        setBackground(backgroundColor);
        setBorder(EMPTY_BORDER);
        setLayout(new BorderLayout());
        add(label, BorderLayout.NORTH);
        add(cardArea, BorderLayout.CENTER);

        var size = new Dimension(panelWidth, labelHeight + contentHeight);
        setPreferredSize(size);
        setMinimumSize(size);
        setMaximumSize(size);
    }

    public void cleanUp() {
        cards.clear();
        cardArea.removeAll();
    }

    public void changeGUISize() {
        layoutCards();
    }

    public void loadCards(CardsView cardsView, BigCard bigCard, UUID gameId) {
        Objects.requireNonNull(cardsView, "cardsView must not be null");
        this.bigCard = bigCard;
        this.gameId = gameId;

        removeMissingCards(cardsView);

        for (CardView cardView : cardsView.values()) {
            MageCard card = cards.get(cardView.getId());
            if (card == null) {
                addCard(cardView);
                continue;
            }
            card.update(cardView);
        }

        layoutCards();
        cardArea.revalidate();
        cardArea.repaint();
        revalidate();
        repaint();
    }

    public int getCardCount() {
        return cards.size();
    }

    public Map<UUID, MageCard> getCardPanels() {
        return cards;
    }

    protected abstract void layoutCards();

    protected MageCard createMageCard(CardView cardView) {
        return Plugins.instance.getMageCard(
                cardView,
                bigCard,
                new CardIconRenderSettings(),
                new Dimension(cardWidth, cardHeight),
                gameId,
                true,
                true,
                PreferencesDialog.getRenderMode(),
                true
        );
    }

    protected final JPanel getCardAreaPanel() {
        return cardArea;
    }

    protected final int getCardWidthPixels() {
        return cardWidth;
    }

    protected final int getCardHeightPixels() {
        return cardHeight;
    }

    protected final int getCardGapPixels() {
        return cardGap;
    }

    protected final int getContentHeightPixels() {
        return contentHeight;
    }

    protected final int getPanelWidthPixels() {
        return panelWidth;
    }

    private void addCard(CardView cardView) {
        MageCard mageCard = createMageCard(cardView);
        mageCard.setCardContainerRef(cardArea);
        mageCard.setZone(zone);
        mageCard.setCardBounds(0, 0, cardWidth, cardHeight);
        mageCard.update(cardView);

        cards.put(cardView.getId(), mageCard);
        cardArea.add(mageCard);
    }

    private void removeMissingCards(CardsView cardsView) {
        Set<UUID> toRemove = new HashSet<>();
        for (UUID id : cards.keySet()) {
            if (!cardsView.containsKey(id)) {
                toRemove.add(id);
            }
        }
        for (UUID id : toRemove) {
            MageCard card = cards.remove(id);
            if (card != null) {
                cardArea.remove(card);
            }
        }
    }

    private JPanel createCardArea(Color overlayTint) {
        JPanel area;
        if (overlayTint == null) {
            area = new JPanel();
        } else {
            area = new JPanel() {
                @Override
                protected void paintChildren(Graphics g) {
                    super.paintChildren(g);
                    if (!cards.isEmpty()) {
                        g.setColor(overlayTint);
                        g.fillRect(0, 0, getWidth(), getHeight());
                    }
                }
            };
        }
        area.setLayout(null);
        area.setBackground(new Color(0, 0, 0, 0));
        area.setOpaque(false);
        return area;
    }
}
