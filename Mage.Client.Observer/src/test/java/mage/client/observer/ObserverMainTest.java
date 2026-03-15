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
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

public class ObserverMainTest {

    private static final String HEALTH_PORT_PROP = "xmage.observer.healthPort";
    private static final String HEALTH_PORT_FILE_PROP = "xmage.observer.healthPortFile";

    private String originalHealthPort;
    private String originalHealthPortFile;
    private ObserverHealthServer healthServer;

    @Before
    public void setUp() {
        originalHealthPort = System.getProperty(HEALTH_PORT_PROP);
        originalHealthPortFile = System.getProperty(HEALTH_PORT_FILE_PROP);
        System.clearProperty(HEALTH_PORT_PROP);
        System.clearProperty(HEALTH_PORT_FILE_PROP);
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
        if (originalHealthPortFile == null) {
            System.clearProperty(HEALTH_PORT_FILE_PROP);
        } else {
            System.setProperty(HEALTH_PORT_FILE_PROP, originalHealthPortFile);
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

    @Test
    public void startConfiguredHealthServerRetriesWhenPortIsBusy() throws Exception {
        try (ServerSocket busySocket = new ServerSocket()) {
            busySocket.bind(new InetSocketAddress("127.0.0.1", 0));
            int busyPort = busySocket.getLocalPort();
            System.setProperty(HEALTH_PORT_PROP, Integer.toString(busyPort));
            healthServer = ObserverMain.startConfiguredHealthServer();
            assertNotNull(healthServer);
            assertTrue("Should bind to a port after the busy one",
                    healthServer.getPort() > busyPort);
        }
    }

    @Test
    public void startConfiguredHealthServerWritesPortFile() throws Exception {
        Path tmpFile = Files.createTempFile("health-port-", ".txt");
        Files.delete(tmpFile);
        try {
            System.setProperty(HEALTH_PORT_PROP, "20000");
            System.setProperty(HEALTH_PORT_FILE_PROP, tmpFile.toString());
            healthServer = ObserverMain.startConfiguredHealthServer();
            assertNotNull(healthServer);
            assertTrue("Port file should be created", Files.exists(tmpFile));
            int writtenPort = Integer.parseInt(Files.readString(tmpFile).trim());
            assertEquals(healthServer.getPort(), writtenPort);
        } finally {
            Files.deleteIfExists(tmpFile);
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
