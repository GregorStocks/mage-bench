package mage.client.observer;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

public class ObserverGameEventLoggerTest {

    @Test
    public void logChatEventWritesStructuredJsonl() throws Exception {
        Path tempDir = Files.createTempDirectory("observer-events-");
        try {
            ObserverGameEventLogger logger = new ObserverGameEventLogger();
            logger.init(tempDir);

            logger.logChatEvent("player_chat", "hello table", "Pilot One");
            logger.logGameOver("done");

            Path logFile = tempDir.resolve("game_events.jsonl");
            assertTrue(Files.exists(logFile));

            List<String> lines = Files.readAllLines(logFile, StandardCharsets.UTF_8);
            assertEquals(2, lines.size());

            JsonObject chatEvent = JsonParser.parseString(lines.get(0)).getAsJsonObject();
            assertEquals("player_chat", chatEvent.get("type").getAsString());
            assertEquals("Pilot One", chatEvent.get("from").getAsString());
            assertEquals("hello table", chatEvent.get("message").getAsString());
            assertEquals(1, chatEvent.get("seq").getAsInt());

            JsonObject gameOverEvent = JsonParser.parseString(lines.get(1)).getAsJsonObject();
            assertEquals("game_over", gameOverEvent.get("type").getAsString());
            assertEquals("done", gameOverEvent.get("message").getAsString());
            assertEquals(2, gameOverEvent.get("seq").getAsInt());
        } finally {
            Files.deleteIfExists(tempDir.resolve("game_events.jsonl"));
            Files.deleteIfExists(tempDir);
        }
    }
}
