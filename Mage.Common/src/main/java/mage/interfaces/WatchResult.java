package mage.interfaces;

import java.io.Serializable;

/**
 * Result of a watch request (roomWatchTable / gameWatchStart), carrying the
 * failure reason across the RPC boundary so clients can report why the server
 * refused the watch instead of a bare false.
 */
public final class WatchResult implements Serializable {

    private static final long serialVersionUID = 1L;

    private static final WatchResult OK = new WatchResult(null);

    private final String failReason; // null == success

    private WatchResult(String failReason) {
        this.failReason = failReason;
    }

    public static WatchResult ok() {
        return OK;
    }

    public static WatchResult fail(String reason) {
        if (reason == null || reason.trim().isEmpty()) {
            throw new IllegalArgumentException("WatchResult.fail requires a non-blank reason");
        }
        return new WatchResult(reason);
    }

    public boolean isSuccess() {
        return failReason == null;
    }

    public String getFailReason() {
        if (failReason == null) {
            throw new IllegalStateException("getFailReason called on a successful WatchResult");
        }
        return failReason;
    }

    @Override
    public String toString() {
        return isSuccess() ? "WatchResult.ok" : "WatchResult.fail(" + failReason + ")";
    }
}
