package mage.client.observer;

import java.awt.*;
import java.util.*;
import java.util.List;
import java.util.UUID;
import javax.swing.*;
import javax.swing.border.Border;
import javax.swing.border.EmptyBorder;

import mage.abilities.icon.CardIconRenderSettings;
import mage.cards.MageCard;
import mage.client.cards.BigCard;
import mage.client.dialog.PreferencesDialog;
import mage.client.plugins.impl.Plugins;
import mage.client.util.GUISizeHelper;
import mage.constants.Zone;
import mage.view.CardView;
import mage.view.CardsView;

/**
 * observer-mode graveyard panel with wider cards and a zone label.
 * Fixed size — cards compress their stack offset to always fit without scrolling.
 */
public class ObserverGraveyardPanel extends JPanel {

    // Instance fields scaled from cardWidth
    private final int cardWidth;
    private final int cardHeight;
    private final int maxStackOffset;
    private final int minStackOffset;
    private final int contentHeight;
    private final int margin;
    private final int labelHeight;
    private final int panelWidth;
    private final int panelHeight;

    private static final Border EMPTY_BORDER = new EmptyBorder(0, 0, 0, 0);
    private static final Color LABEL_COLOR = new Color(140, 140, 180);

    private final Map<UUID, MageCard> cards = new LinkedHashMap<>();
    private JPanel cardArea;
    private BigCard bigCard;
    private UUID gameId;

    public ObserverGraveyardPanel() {
        this(80);
    }

    public ObserverGraveyardPanel(int cardWidth) {
        this.cardWidth = cardWidth;
        this.cardHeight = (int) (cardWidth * GUISizeHelper.CARD_WIDTH_TO_HEIGHT_COEF);
        double scale = cardWidth / 80.0;
        this.maxStackOffset = Math.max(5, (int) (24 * scale));
        this.minStackOffset = Math.max(3, (int) (5 * scale));
        this.contentHeight = cardHeight * 2;
        this.margin = Math.max(3, (int) (5 * scale));
        this.labelHeight = Math.max(14, (int) (14 * scale));
        this.panelWidth = cardWidth + 2 * margin;
        this.panelHeight = labelHeight + contentHeight;
        initComponents();
    }

    private void initComponents() {
        cardArea = new JPanel();
        cardArea.setLayout(null); // Absolute positioning for stacked cards
        cardArea.setBackground(new Color(0, 0, 0, 0));
        cardArea.setOpaque(false);

        var labelFont = new Font(Font.SANS_SERIF, Font.BOLD, Math.max(10, (int) (10 * cardWidth / 80.0)));
        var label = new JLabel("GY");
        label.setFont(labelFont);
        label.setForeground(LABEL_COLOR);
        label.setPreferredSize(new Dimension(0, labelHeight));
        label.setBorder(new EmptyBorder(1, margin, 0, 0));

        setOpaque(true);
        setBackground(new Color(50, 50, 80)); // Dark blue-gray
        setBorder(EMPTY_BORDER);
        setLayout(new BorderLayout());
        add(label, BorderLayout.NORTH);
        add(cardArea, BorderLayout.CENTER);

        var size = new Dimension(panelWidth, panelHeight);
        setPreferredSize(size);
        setMinimumSize(size);
        setMaximumSize(size);
    }

    public void cleanUp() {
        cards.clear();
        cardArea.removeAll();
    }

    public void loadCards(CardsView cardsView, BigCard bigCard, UUID gameId) {
        this.bigCard = bigCard;
        this.gameId = gameId;

        // Remove cards no longer in graveyard
        var toRemove = new HashSet<UUID>();
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

        // Add/update cards
        for (CardView cardView : cardsView.values()) {
            if (!cards.containsKey(cardView.getId())) {
                addCard(cardView);
            } else {
                cards.get(cardView.getId()).update(cardView);
            }
        }

        layoutCards();
        cardArea.revalidate();
        cardArea.repaint();
    }

    private void addCard(CardView cardView) {
        var cardDimension = new Dimension(cardWidth, cardHeight);
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
        mageCard.setZone(Zone.GRAVEYARD);
        mageCard.setCardBounds(0, 0, cardWidth, cardHeight);
        mageCard.update(cardView);

        cards.put(cardView.getId(), mageCard);
        cardArea.add(mageCard);
    }

    private void layoutCards() {
        if (cards.isEmpty()) {
            return;
        }

        var cardList = new ArrayList<>(cards.values());
        int n = cardList.size();

        // Dynamically compute stack offset so all cards fit in contentHeight
        int offset;
        if (n <= 1) {
            offset = 0;
        } else {
            int availableForOffsets = contentHeight - cardHeight;
            offset = Math.min(maxStackOffset, availableForOffsets / (n - 1));
            offset = Math.max(minStackOffset, offset);
        }

        int y = 0;
        for (int i = 0; i < n; i++) {
            MageCard card = cardList.get(i);
            card.setCardBounds(0, y, cardWidth, cardHeight);
            cardArea.setComponentZOrder(card, n - 1 - i);
            if (i < n - 1) {
                y += offset;
            }
        }

        cardArea.setPreferredSize(new Dimension(panelWidth, contentHeight));
    }

    public int getCardCount() {
        return cards.size();
    }

    /**
     * Return card components keyed by card id.
     */
    public Map<UUID, MageCard> getCardPanels() {
        return cards;
    }
}
