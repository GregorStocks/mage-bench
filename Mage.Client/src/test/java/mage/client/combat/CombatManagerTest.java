package mage.client.combat;

import org.junit.Test;

import javax.swing.JPanel;
import java.awt.Point;

import static org.junit.Assert.assertNull;

public class CombatManagerTest {

    @Test
    public void hiddenComponentsDoNotRequireScreenCoordinates() {
        JPanel hiddenPanel = new JPanel();

        Point location = CombatManager.instance.getLocationOnScreenIfShowing(hiddenPanel);

        assertNull("Expected hidden components to skip on-screen coordinate lookup", location);
    }
}
