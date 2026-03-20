package mage.client.combat;

import mage.cards.MageCard;
import mage.cards.MageCardAnimationSettings;
import mage.cards.MageCardLocation;
import mage.cards.MageCardSpace;
import mage.constants.Zone;
import mage.view.CardView;
import org.junit.Test;

import javax.swing.*;
import java.awt.*;
import java.util.Collections;
import java.util.List;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

public class CombatManagerTest {

    @Test
    public void locationOnScreenIfShowingSkipsHiddenComponents() {
        TrackingComponent component = new TrackingComponent(false);

        Point location = CombatManager.getLocationOnScreenIfShowing(component);

        assertNull("Hidden components must not query on-screen coordinates", location);
        assertFalse("Hidden components must not call getLocationOnScreen", component.locationRequested);
    }

    @Test
    public void cardPointOnScreenIfShowingSkipsHiddenCards() {
        TrackingMageCard card = new TrackingMageCard(false);

        Point location = CombatManager.getCardPointOnScreenIfShowing(card);

        assertNull("Hidden cards must not query on-screen coordinates", location);
        assertFalse("Hidden cards must not call getLocationOnScreen", card.locationRequested);
    }

    @Test
    public void cardPointOnScreenIfShowingReturnsVisibleCardLocation() {
        TrackingMageCard card = new TrackingMageCard(true);

        Point location = CombatManager.getCardPointOnScreenIfShowing(card);

        assertTrue("Visible cards must use on-screen coordinates", card.locationRequested);
        assertTrue("Expected the visible card location to be returned", new Point(17, 29).equals(location));
    }

    private static final class TrackingComponent extends JComponent {

        private final boolean showing;
        private boolean locationRequested;

        private TrackingComponent(boolean showing) {
            this.showing = showing;
        }

        @Override
        public boolean isShowing() {
            return showing;
        }

        @Override
        public Point getLocationOnScreen() {
            locationRequested = true;
            return new Point(11, 23);
        }
    }

    private static final class TrackingMageCard extends MageCard {

        private final boolean showing;
        private boolean locationRequested;

        private TrackingMageCard(boolean showing) {
            this.showing = showing;
        }

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
        }

        @Override
        public float getAlpha() {
            return 1.0f;
        }

        @Override
        public CardView getOriginal() {
            return null;
        }

        @Override
        public void setCardCaptionTopOffset(int yOffsetPercent) {
        }

        @Override
        public void setCardBounds(int x, int y, int width, int height) {
        }

        @Override
        public void update(CardView card) {
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
        }

        @Override
        public Zone getZone() {
            return null;
        }

        @Override
        public void toggleTransformed() {
        }

        @Override
        public boolean isTransformed() {
            return false;
        }

        @Override
        public void showCardTitle() {
        }

        @Override
        public void setSelected(boolean selected) {
        }

        @Override
        public void setCardContainerRef(Container cardContainer) {
        }

        @Override
        public void setTopPanelRef(MageCard mageCard) {
        }

        @Override
        public MageCard getTopPanelRef() {
            return this;
        }

        @Override
        public Container getCardContainer() {
            return null;
        }

        @Override
        public void setChoosable(boolean isChoosable) {
        }

        @Override
        public boolean isChoosable() {
            return false;
        }

        @Override
        public void setPopupMenu(JPopupMenu popupMenu) {
        }

        @Override
        public JPopupMenu getPopupMenu() {
            return null;
        }

        @Override
        public void cleanUp() {
        }

        @Override
        public int getCardWidth() {
            return 80;
        }

        @Override
        public int getCardHeight() {
            return 120;
        }

        @Override
        public MageCardAnimationSettings getAnimationSettings(int offsetX, int offsetY, float cardBoundWidth, float cardBoundHeight) {
            return null;
        }

        @Override
        public List<MageCard> getLinks() {
            return Collections.emptyList();
        }

        @Override
        public MageCardSpace getOuterSpace() {
            return MageCardSpace.empty;
        }

        @Override
        public MageCardLocation getCardLocation() {
            return new MageCardLocation(new Point(17, 29), MageCardSpace.empty, new Rectangle(80, 120));
        }

        @Override
        public boolean isShowing() {
            return showing;
        }

        @Override
        public Point getLocationOnScreen() {
            locationRequested = true;
            return new Point(17, 29);
        }
    }
}
