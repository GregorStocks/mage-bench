package mage.client.observer;

import org.junit.After;
import org.junit.Before;
import org.junit.Test;

import java.io.InputStream;
import java.net.InetSocketAddress;
import java.net.HttpURLConnection;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URL;
import java.nio.charset.StandardCharsets;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

public class ObserverMainTest {

    private static final String HEALTH_PORT_PROP = "xmage.observer.healthPort";

    private String originalHealthPort;
    private ObserverHealthServer healthServer;

    @Before
    public void setUp() {
        originalHealthPort = System.getProperty(HEALTH_PORT_PROP);
        System.clearProperty(HEALTH_PORT_PROP);
    }

    @After
    public void tearDown() {
        if (healthServer != null) {
            healthServer.stop();
            healthServer = null;
        }
        if (originalHealthPort == null) {
            System.clearProperty(HEALTH_PORT_PROP);
        } else {
            System.setProperty(HEALTH_PORT_PROP, originalHealthPort);
        }
    }

    @Test
    public void startConfiguredHealthServerSkipsWhenPortUnset() {
        assertNull(ObserverMain.startConfiguredHealthServer());
    }

    @Test
    public void startHealthServerBindsPortImmediately() throws Exception {
        healthServer = ObserverMain.startHealthServer(0);

        assertNotNull(healthServer);

        try (Socket socket = new Socket()) {
            socket.connect(new InetSocketAddress("127.0.0.1", healthServer.getPort()), 1000);
            assertTrue(socket.isConnected());
        }
    }

    @Test(expected = RuntimeException.class)
    public void startConfiguredHealthServerFailsWhenPortIsBusy() throws Exception {
        try (ServerSocket busySocket = new ServerSocket()) {
            busySocket.bind(new InetSocketAddress("127.0.0.1", 0));
            System.setProperty(HEALTH_PORT_PROP, Integer.toString(busySocket.getLocalPort()));
            ObserverMain.startConfiguredHealthServer();
        }
    }

    @Test
    public void healthServerWaitForCommandsUsesSeparateReadinessLatch() throws Exception {
        healthServer = ObserverMain.startHealthServer(0);

        HttpURLConnection timeoutConn = openGet("/wait-for-commands?timeout=1");
        assertEquals(408, timeoutConn.getResponseCode());
        assertTrue(readBody(timeoutConn).contains("\"status\":\"timeout\""));

        healthServer.signalKeepAliveReady();

        HttpURLConnection readyConn = openGet("/wait-for-commands?timeout=1");
        assertEquals(200, readyConn.getResponseCode());
        assertTrue(readBody(readyConn).contains("\"status\":\"ready\""));
    }

    private HttpURLConnection openGet(String path) throws Exception {
        URL url = new URL("http://127.0.0.1:" + healthServer.getPort() + path);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("GET");
        conn.setConnectTimeout(1000);
        conn.setReadTimeout(2000);
        return conn;
    }

    private static String readBody(HttpURLConnection conn) throws Exception {
        InputStream stream = conn.getResponseCode() >= 400 ? conn.getErrorStream() : conn.getInputStream();
        try (stream) {
            return new String(stream.readAllBytes(), StandardCharsets.UTF_8);
        }
    }
}
