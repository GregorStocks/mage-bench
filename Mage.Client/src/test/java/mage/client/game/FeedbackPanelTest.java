package mage.client.game;

import org.junit.Test;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.UUID;
import java.util.concurrent.ScheduledFuture;

import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

public class FeedbackPanelTest {

    @Test
    public void invalidatingFeedbackCancelsPendingAutoCloseTask() throws Exception {
        FeedbackPanel panel = new FeedbackPanel();
        setPrivateField(panel, "gameId", UUID.randomUUID());
        setPrivateField(panel, "mode", FeedbackPanel.FeedbackMode.END);

        Method endWithTimeout = FeedbackPanel.class.getDeclaredMethod("endWithTimeout");
        endWithTimeout.setAccessible(true);
        endWithTimeout.invoke(panel);

        ScheduledFuture<?> pendingTask = getPendingTask(panel);
        assertNotNull("Expected END mode to schedule an auto-close task", pendingTask);

        Method invalidate = FeedbackPanel.class.getDeclaredMethod("invalidateAutoCloseLocked");
        invalidate.setAccessible(true);
        invalidate.invoke(panel);

        assertTrue("Expected the pending auto-close task to be cancelled", pendingTask.isCancelled());
        assertNull("Expected the panel to clear its pending auto-close task", getPendingTask(panel));
    }

    private static void setPrivateField(Object target, String fieldName, Object value) throws Exception {
        Field field = target.getClass().getDeclaredField(fieldName);
        field.setAccessible(true);
        field.set(target, value);
    }

    private static ScheduledFuture<?> getPendingTask(FeedbackPanel panel) throws Exception {
        Field field = FeedbackPanel.class.getDeclaredField("pendingAutoCloseTask");
        field.setAccessible(true);
        return (ScheduledFuture<?>) field.get(panel);
    }
}
