package mage.client.observer;

import mage.view.ChatMessage.MessageColor;
import mage.view.ChatMessage.MessageType;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;

import java.util.Date;

import static org.junit.Assert.assertEquals;

public class CombinedChatPanelTest {

    private static final String NO_WINDOW_PROP = "xmage.observer.noWindow";

    private String originalNoWindow;

    @Before
    public void setUp() {
        originalNoWindow = System.getProperty(NO_WINDOW_PROP);
    }

    @After
    public void tearDown() {
        if (originalNoWindow == null) {
            System.clearProperty(NO_WINDOW_PROP);
        } else {
            System.setProperty(NO_WINDOW_PROP, originalNoWindow);
        }
    }

    @Test
    public void noWindowModeSkipsGameLogRendering() {
        System.setProperty(NO_WINDOW_PROP, "true");
        CombinedChatPanel panel = new CombinedChatPanel();

        String before = panel.getText();
        panel.receiveMessage("spectator", " watches the game", new Date(0), "T1", MessageType.GAME, MessageColor.BLACK);

        assertEquals(before, panel.getText());
    }
}
