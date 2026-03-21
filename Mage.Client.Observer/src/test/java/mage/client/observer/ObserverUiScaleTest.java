package mage.client.observer;

import org.junit.Test;

import static org.junit.Assert.assertEquals;

public class ObserverUiScaleTest {

    @Test
    public void scaleFactorClampsToConfiguredRange() {
        assertEquals(1.0, ObserverUiScale.scaleFactorForHeight(720), 0.0001);
        assertEquals(1.0, ObserverUiScale.scaleFactorForHeight(1080), 0.0001);
        assertEquals(2.0, ObserverUiScale.scaleFactorForHeight(2160), 0.0001);
        assertEquals(2.5, ObserverUiScale.scaleFactorForHeight(4000), 0.0001);
    }

    @Test
    public void avatarSizeClampsToConfiguredRange() {
        assertEquals(80, ObserverUiScale.avatarSizeForHeight(720));
        assertEquals(98, ObserverUiScale.avatarSizeForHeight(1080));
        assertEquals(196, ObserverUiScale.avatarSizeForHeight(2160));
        assertEquals(300, ObserverUiScale.avatarSizeForHeight(4000));
    }
}
