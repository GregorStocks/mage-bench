package mage.client.observer;

import java.awt.*;
import java.util.*;
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
import org.apache.log4j.Logger;

/**
 * Panel for displaying commander cards in the observer/spectator west panel.
 * Always visible (shows empty placeholder when no commanders loaded).
 * Supports partner commanders displayed side-by-side.
 */
public class CommanderPanel extends JPanel {

    private static final Logger logger = Logger.getLogger(CommanderPanel.class);

    private static final Border EMPTY_BORDER = new EmptyBorder(2, 2, 2, 2);
    private static final Color LABEL_COLOR = new Color(200, 180, 120);

    // Instance fields scaled from cardWidth
    private final int cardWidth;
    private final int cardHeight;
    private final int cardGap;
    private final int margin;
    private final Font labelFont;
    private final int labelHeight;

    private final Map<UUID, MageCard> cards = new LinkedHashMap<>();
    private JPanel cardArea;
    private BigCard bigCard;
    private UUID gameId;

    public CommanderPanel() {
        this(80);
    }

    public CommanderPanel(int cardWidth) {
        this.cardWidth = cardWidth;
        this.cardHeight = (int) (cardWidth * GUISizeHelper.CARD_WIDTH_TO_HEIGHT_COEF);
        double scale = cardWidth / 80.0;
        this.cardGap = Math.max(3, (int) (5 * scale));
        this.margin = Math.max(3, (int) (5 * scale));
        this.labelFont = new Font(Font.SANS_SERIF, Font.BOLD, Math.max(10, (int) (10 * scale)));
        this.labelHeight = Math.max(14, (int) (14 * scale));
        initComponents();
    }

    private void initComponents() {
        cardArea = new JPanel();
        cardArea.setLayout(null); // Absolute positioning
        cardArea.setBackground(new Color(0, 0, 0, 0));
        cardArea.setOpaque(false);

        var label = new JLabel("CMD");
        label.setFont(labelFont);
        label.setForeground(LABEL_COLOR);
        label.setPreferredSize(new Dimension(0, labelHeight));
        label.setBorder(new EmptyBorder(1, margin, 0, 0));

        setOpaque(true);
        setBackground(new Color(100, 80, 40)); // Gold/amber for commander zone
        setBorder(EMPTY_BORDER);
        setLayout(new BorderLayout());
        add(label, BorderLayout.NORTH);
        add(cardArea, BorderLayout.CENTER);

        // Fixed size — always visible, does not grow/shrink
        int panelWidth = cardWidth + 2 * margin;
        int panelHeight = labelHeight + cardHeight + 4;
        var size = new Dimension(panelWidth, panelHeight);
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
        this.bigCard = bigCard;
        this.gameId = gameId;

        logger.info("CommanderPanel.loadCards called with " + (cardsView != null ? cardsView.size() : "null") + " cards");

        // Remove cards no longer in command zone
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
        revalidate();
        repaint();

        logger.info("CommanderPanel after load: " + cards.size() + " cards");
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
        mageCard.setZone(Zone.COMMAND);
        mageCard.setCardBounds(0, 0, cardWidth, cardHeight);
        mageCard.update(cardView);

        cards.put(cardView.getId(), mageCard);
        cardArea.add(mageCard);
    }

    private void layoutCards() {
        // Position cards horizontally with absolute positioning
        int x = 2; // Small left margin
        for (MageCard card : cards.values()) {
            card.setCardBounds(x, 2, cardWidth, cardHeight);
            x += cardWidth + cardGap;
        }
        // Panel size is fixed — set in initComponents, not updated here
    }

    /**
     * Get the number of cards currently displayed.
     */
    public int getCardCount() {
        return cards.size();
    }

    /**
     * Return commander card components keyed by card id.
     */
    public Map<UUID, MageCard> getCardPanels() {
        return cards;
    }
}
