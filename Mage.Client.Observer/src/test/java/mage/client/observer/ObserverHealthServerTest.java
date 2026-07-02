package mage.client.observer;

import org.junit.After;
import org.junit.Before;
import org.junit.Test;

import java.net.HttpURLConnection;

import static mage.client.observer.ObserverHttpTestSupport.postJson;
import static mage.client.observer.ObserverHttpTestSupport.readBody;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

public class ObserverHealthServerTest {

    private ObserverHealthServer healthServer;

    @Before
    public void setUp() throws Exception {
        healthServer = new ObserverHealthServer(0);
        healthServer.start();
    }

    @After
    public void tearDown() {
        healthServer.stop();
    }

    @Test
    public void waitForWatchingReturnsWatchingAfterSignal() throws Exception {
        healthServer.signalGameWatching("/tmp/game-a");

        HttpURLConnection conn = postWaitForWatching("/tmp/game-a", 1);
        assertEquals(200, conn.getResponseCode());
        assertTrue(readBody(conn).contains("\"watching\":true"));
    }

    @Test
    public void waitForWatchingTimesOutWithoutSignal() throws Exception {
        HttpURLConnection conn = postWaitForWatching("/tmp/game-b", 1);
        assertEquals(408, conn.getResponseCode());
        assertTrue(readBody(conn).contains("\"error\":\"timeout\""));
    }

    @Test
    public void waitForWatchingSurfacesFailureReason() throws Exception {
        healthServer.signalGameWatchFailed("/tmp/game-c", "watchTable returned false for table 123");

        HttpURLConnection conn = postWaitForWatching("/tmp/game-c", 5);
        assertEquals(502, conn.getResponseCode());
        String body = readBody(conn);
        assertTrue("Body should report not watching: " + body, body.contains("\"watching\":false"));
        assertTrue("Body should include the failure reason: " + body,
                body.contains("watchTable returned false for table 123"));
    }

    @Test
    public void watchFailureAfterSuccessIsIgnored() throws Exception {
        healthServer.signalGameWatching("/tmp/game-d");
        healthServer.signalGameWatchFailed("/tmp/game-d", "late failure");

        HttpURLConnection conn = postWaitForWatching("/tmp/game-d", 1);
        assertEquals(200, conn.getResponseCode());
        assertTrue(readBody(conn).contains("\"watching\":true"));
    }

    private HttpURLConnection postWaitForWatching(String gameDir, int timeoutSeconds) throws Exception {
        String body = "{\"gameDir\":\"" + gameDir + "\",\"timeout\":" + timeoutSeconds + "}";
        return postJson(healthServer.getPort(), "/wait-for-watching", body, (timeoutSeconds + 5) * 1000);
    }
}
