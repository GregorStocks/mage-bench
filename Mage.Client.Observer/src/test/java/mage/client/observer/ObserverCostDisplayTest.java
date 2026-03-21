package mage.client.observer;

import org.junit.Test;

import java.util.Set;

import static org.junit.Assert.assertEquals;
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
}
