package mage.interfaces.callback;

import mage.game.BridgeLogEntry;
import mage.remote.traffic.ZippedObject;
import mage.utils.CompressUtil;
import mage.util.ThreadUtils;

import java.io.Serializable;
import java.util.List;
import java.util.UUID;

/**
 * Network: server's event to proccess on client side
 *
 * @author BetaSteward_at_googlemail.com
 */
public class ClientCallback implements Serializable {

    // for debug only: simulate bad connection on client side, use launcher's client param like -Dxmage.badConnection
    private static final String SIMULATE_BAD_CONNECTION_PROP = "xmage.badConnection";
    public static final boolean SIMULATE_BAD_CONNECTION;

    static {
        SIMULATE_BAD_CONNECTION = System.getProperty(SIMULATE_BAD_CONNECTION_PROP) != null;
    }

    private UUID objectId;
    private Object data;
    private ClientCallbackMethod method;
    private int messageId;
    private List<BridgeLogEntry> bridgeEvents;

    public ClientCallback(ClientCallbackMethod method, UUID objectId) {
        this(method, objectId, null);
    }

    public ClientCallback(ClientCallbackMethod method, UUID objectId, Object data) {
        this(method, objectId, data, true);
    }

    public ClientCallback(ClientCallbackMethod method, UUID objectId, Object data, boolean useCompress) {
        this.method = method;
        this.objectId = objectId;
        this.setData(data, useCompress);
    }

    /**
     * Copy for fan-out: broadcasts reuse one instance across sessions, but each session
     * assigns its own messageId, so it must mutate a private copy (data is shared by
     * reference and never re-compressed; messageId starts fresh).
     */
    public ClientCallback(ClientCallback other) {
        this.method = other.method;
        this.objectId = other.objectId;
        this.data = other.data;
        this.bridgeEvents = other.bridgeEvents;
    }

    private void simulateBadConnection() {
        if (SIMULATE_BAD_CONNECTION) {
            ThreadUtils.sleep(100);
        }
    }

    public void clear() {
        method = null;
        data = null;
    }

    public UUID getObjectId() {
        return objectId;
    }

    public void setObjectId(UUID objectId) {
        this.objectId = objectId;
    }

    public Object getData() {
        if (this.data instanceof ZippedObject) {
            throw new IllegalStateException("Client data must be decompressed first");
        }
        return data;
    }

    public void setData(Object data, boolean useCompress) {
        if (!useCompress || data == null || data instanceof ZippedObject) {
            this.data = data;
        } else {
            this.data = CompressUtil.compress(data);
            simulateBadConnection();
        }
    }

    public void decompressData() {
        if (this.data instanceof ZippedObject) {
            this.data = CompressUtil.decompress(this.data);
            simulateBadConnection();
        }
    }

    public ClientCallbackMethod getMethod() {
        return method;
    }

    public void setMethod(ClientCallbackMethod method) {
        this.method = method;
    }

    public void setMessageId(int messageId) {
        this.messageId = messageId;
    }

    public int getMessageId() {
        return messageId;
    }

    public List<BridgeLogEntry> getBridgeEvents() {
        return bridgeEvents;
    }

    public void setBridgeEvents(List<BridgeLogEntry> bridgeEvents) {
        this.bridgeEvents = bridgeEvents;
    }

    public String getInfo() {
        return String.format("message %d - %s - %s", this.getMessageId(), this.getMethod().getType(), this.getMethod());
    }
}
