package mage.client.observer;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/** Shared HTTP helpers for tests that talk to the observer health server. */
final class ObserverHttpTestSupport {

    private ObserverHttpTestSupport() {
    }

    static HttpURLConnection openGet(int port, String path) throws Exception {
        HttpURLConnection conn = openConnection(port, path, 2000);
        conn.setRequestMethod("GET");
        return conn;
    }

    static HttpURLConnection postJson(int port, String path, String body, int readTimeoutMs) throws Exception {
        HttpURLConnection conn = openConnection(port, path, readTimeoutMs);
        conn.setRequestMethod("POST");
        conn.setDoOutput(true);
        try (OutputStream os = conn.getOutputStream()) {
            os.write(body.getBytes(StandardCharsets.UTF_8));
        }
        return conn;
    }

    static String readBody(HttpURLConnection conn) throws Exception {
        InputStream stream = conn.getResponseCode() >= 400 ? conn.getErrorStream() : conn.getInputStream();
        try (stream) {
            return new String(stream.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    private static HttpURLConnection openConnection(int port, String path, int readTimeoutMs) throws Exception {
        URL url = new URL("http://127.0.0.1:" + port + path);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setConnectTimeout(1000);
        conn.setReadTimeout(readTimeoutMs);
        return conn;
    }
}
