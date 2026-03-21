package mage.client.observer;

import org.junit.Test;

import java.lang.reflect.Field;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Set;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class ObserverCostDisplayTest {

    @Test
    public void parseLlmPlayerNamesSelectsOnlyPilotEntries() {
        String configJson = "{"
                + "\"players\":["
                + "{\"name\":\"Pilot One\",\"type\":\"pilot\"},"
                + "{\"name\":\"Human\",\"type\":\"human\"},"
                + "{\"name\":\"Pilot Two\",\"type\":\"pilot\"}"
                + "]}";

        Set<String> llmPlayers = ObserverCostDisplay.parseLlmPlayerNames(configJson);

        assertEquals(Set.of("Pilot One", "Pilot Two"), llmPlayers);
    }

    @Test
    public void parseLlmPlayerNamesReturnsEmptySetForMissingOrInvalidConfig() {
        assertTrue(ObserverCostDisplay.parseLlmPlayerNames(null).isEmpty());
        assertTrue(ObserverCostDisplay.parseLlmPlayerNames("").isEmpty());
        assertTrue(ObserverCostDisplay.parseLlmPlayerNames("{").isEmpty());
    }

    @Test
    public void formatCostUsesFourDecimalPlaces() {
        assertEquals("$0.1250", ObserverCostDisplay.formatCost(0.125));
    }

    @Test
    public void initDoesNotLatchWithoutGameDirPath() throws Exception {
        ObserverCostDisplay display = new ObserverCostDisplay();
        String configJson = "{\"players\":[{\"name\":\"Pilot One\",\"type\":\"pilot\"}]}";

        display.init(null, configJson);
        assertFalse(isCostPollingInitialized(display));

        Path tempDir = Files.createTempDirectory("observer-cost-display-");
        try {
            display.init(tempDir, configJson);
            assertTrue(isCostPollingInitialized(display));
        } finally {
            Files.deleteIfExists(tempDir);
        }
    }

    private static boolean isCostPollingInitialized(ObserverCostDisplay display) throws Exception {
        Field field = ObserverCostDisplay.class.getDeclaredField("costPollingInitialized");
        field.setAccessible(true);
        return field.getBoolean(display);
    }
}
