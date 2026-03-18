package mage.client.observer;

import mage.cards.MageCard;
import mage.cards.MageCardAnimationSettings;
import mage.cards.MageCardSpace;
import mage.constants.Zone;
import mage.view.CardView;
import mage.view.CardsView;
import org.junit.Test;

import javax.swing.*;
import java.awt.*;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

public class ObserverZonePanelTest {

    @Test
    public void commanderPanelSpacesCardsHorizontally() {
        TestCommanderPanel panel = new TestCommanderPanel(80);
        TestMageCard first = panel.seedCard();
        TestMageCard second = panel.seedCard();

        panel.changeGUISize();

        assertEquals(new Rectangle(2, 2, 80, expectedCardHeight(80)), first.getBounds());
        assertEquals(new Rectangle(87, 2, 80, expectedCardHeight(80)), second.getBounds());
        assertEquals(expectedPanelSize(80, 1, 4), panel.getPreferredSize());
    }

    @Test
    public void stackedPanelsCompressOffsetsToFitContentHeight() {
        TestGraveyardPanel panel = new TestGraveyardPanel(80);
        List<TestMageCard> cards = new ArrayList<>();
        for (int i = 0; i < 12; i++) {
            cards.add(panel.seedCard());
        }

        panel.changeGUISize();

        assertEquals(expectedContentSize(80, 2), panel.cardAreaPreferredSize());
        assertEquals(0, cards.get(0).getBounds().y);
        assertEquals(10, cards.get(1).getBounds().y);
        assertEquals(110, cards.get(11).getBounds().y);
        assertEquals(11, panel.cardZOrder(cards.get(0)));
        assertEquals(0, panel.cardZOrder(cards.get(11)));
    }

    @Test
    public void cleanUpRemovesSeededCardsAndComponents() {
        TestCommanderPanel panel = new TestCommanderPanel(80);
        panel.seedCard();
        panel.seedCard();

        panel.cleanUp();

        assertTrue(panel.getCardPanels().isEmpty());
        assertEquals(0, panel.cardAreaComponentCount());
    }

    @Test
    public void loadCardsWithEmptyViewClearsExistingCards() {
        TestGraveyardPanel panel = new TestGraveyardPanel(80);
        panel.seedCard();
        panel.seedCard();
        assertEquals(2, panel.getCardCount());
        assertEquals(2, panel.cardAreaComponentCount());

        panel.loadCards(new CardsView(), null, UUID.randomUUID());

        assertEquals(0, panel.getCardCount());
        assertEquals(0, panel.cardAreaComponentCount());
    }

    @Test
    public void concretePanelsExposeExpectedLabelsAndSizes() {
        assertPanelChrome(new CommanderPanel(80), "CMD", new Color(100, 80, 40), expectedPanelSize(80, 1, 4));
        assertPanelChrome(new ObserverGraveyardPanel(80), "GY", new Color(50, 50, 80), expectedPanelSize(80, 2, 0));
        assertPanelChrome(new ObserverExilePanel(80, 3), "EXILE", new Color(90, 40, 40), expectedPanelSize(80, 3, 0));
    }

    private static void assertPanelChrome(JPanel panel, String expectedLabel, Color background, Dimension size) {
        assertEquals(expectedLabel, findLabel(panel).getText());
        assertEquals(background, panel.getBackground());
        assertEquals(size, panel.getPreferredSize());
        assertEquals(size, panel.getMinimumSize());
        assertEquals(size, panel.getMaximumSize());
    }

    private static JLabel findLabel(JPanel panel) {
        for (Component component : panel.getComponents()) {
            if (component instanceof JLabel label) {
                return label;
            }
        }
        throw new AssertionError("panel label not found");
    }

    private static int expectedCardHeight(int cardWidth) {
        return (int) (cardWidth * mage.client.util.GUISizeHelper.CARD_WIDTH_TO_HEIGHT_COEF);
    }

    private static Dimension expectedContentSize(int cardWidth, int contentHeightMultiplier) {
        int margin = Math.max(3, (int) (5 * (cardWidth / 80.0)));
        int panelWidth = cardWidth + 2 * margin;
        int cardHeight = expectedCardHeight(cardWidth);
        return new Dimension(panelWidth, cardHeight * contentHeightMultiplier);
    }

    private static Dimension expectedPanelSize(int cardWidth, int contentHeightMultiplier, int contentHeightPadding) {
        int labelHeight = Math.max(14, (int) (14 * (cardWidth / 80.0)));
        int cardHeight = expectedCardHeight(cardWidth);
        Dimension contentSize = expectedContentSize(cardWidth, contentHeightMultiplier);
        return new Dimension(contentSize.width, labelHeight + cardHeight * contentHeightMultiplier + contentHeightPadding);
    }

    private static final class TestCommanderPanel extends CommanderPanel {
        TestCommanderPanel(int cardWidth) {
            super(cardWidth);
        }

        TestMageCard seedCard() {
            TestMageCard card = new TestMageCard();
            getCardPanels().put(UUID.randomUUID(), card);
            getCardAreaPanel().add(card);
            return card;
        }

        int cardAreaComponentCount() {
            return getCardAreaPanel().getComponentCount();
        }
    }

    private static final class TestGraveyardPanel extends ObserverGraveyardPanel {
        TestGraveyardPanel(int cardWidth) {
            super(cardWidth);
        }

        TestMageCard seedCard() {
            TestMageCard card = new TestMageCard();
            getCardPanels().put(UUID.randomUUID(), card);
            getCardAreaPanel().add(card);
            return card;
        }

        Dimension cardAreaPreferredSize() {
            return getCardAreaPanel().getPreferredSize();
        }

        int cardZOrder(TestMageCard card) {
            return getCardAreaPanel().getComponentZOrder(card);
        }

        int cardAreaComponentCount() {
            return getCardAreaPanel().getComponentCount();
        }
    }

    private static final class TestMageCard extends MageCard {
        private CardView original;
        private Zone zone;
        private float alpha;
        private boolean transformed;
        private boolean choosable;
        private JPopupMenu popupMenu;
        private Container cardContainer;
        private MageCard topPanelRef = this;

        @Override
        public void onBeginAnimation() {
        }

        @Override
        public void onEndAnimation() {
        }

        @Override
        public boolean isTapped() {
            return false;
        }

        @Override
        public boolean isFlipped() {
            return false;
        }

        @Override
        public void setAlpha(float transparency) {
            this.alpha = transparency;
        }

        @Override
        public float getAlpha() {
            return alpha;
        }

        @Override
        public CardView getOriginal() {
            return original;
        }

        @Override
        public void setCardCaptionTopOffset(int yOffsetPercent) {
        }

        @Override
        public void setCardBounds(int x, int y, int width, int height) {
            super.setBounds(x, y, width, height);
        }

        @Override
        public void update(CardView card) {
            this.original = card;
        }

        @Override
        public void updateArtImage() {
        }

        @Override
        public Image getImage() {
            return null;
        }

        @Override
        public void setZone(Zone zone) {
            this.zone = zone;
        }

        @Override
        public Zone getZone() {
            return zone;
        }

        @Override
        public void toggleTransformed() {
            transformed = !transformed;
        }

        @Override
        public boolean isTransformed() {
            return transformed;
        }

        @Override
        public void showCardTitle() {
        }

        @Override
        public void setSelected(boolean selected) {
        }

        @Override
        public void setCardContainerRef(Container cardContainer) {
            this.cardContainer = cardContainer;
        }

        @Override
        public void setTopPanelRef(MageCard mageCard) {
            this.topPanelRef = mageCard;
        }

        @Override
        public MageCard getTopPanelRef() {
            return topPanelRef;
        }

        @Override
        public Container getCardContainer() {
            return cardContainer;
        }

        @Override
        public void setChoosable(boolean isChoosable) {
            this.choosable = isChoosable;
        }

        @Override
        public boolean isChoosable() {
            return choosable;
        }

        @Override
        public void setPopupMenu(JPopupMenu popupMenu) {
            this.popupMenu = popupMenu;
        }

        @Override
        public JPopupMenu getPopupMenu() {
            return popupMenu;
        }

        @Override
        public void cleanUp() {
        }

        @Override
        public int getCardWidth() {
            return getBounds().width;
        }

        @Override
        public int getCardHeight() {
            return getBounds().height;
        }

        @Override
        public MageCardAnimationSettings getAnimationSettings(int offsetX, int offsetY, float cardBoundWidth, float cardBoundHeight) {
            return new MageCardAnimationSettings();
        }

        @Override
        public List<MageCard> getLinks() {
            return List.of();
        }

        @Override
        public MageCardSpace getOuterSpace() {
            return MageCardSpace.empty;
        }
    }
}
