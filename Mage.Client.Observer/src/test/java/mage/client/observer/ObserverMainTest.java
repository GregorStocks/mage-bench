package mage.client.observer;

import org.junit.After;
import org.junit.Before;
import org.junit.Test;

import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;

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
}
