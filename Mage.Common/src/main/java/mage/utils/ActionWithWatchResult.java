package mage.utils;

import mage.interfaces.ActionWithResult;
import mage.interfaces.WatchResult;

/**
 * Used to write less code for ActionWithResult anonymous classes with WatchResult return type.
 */
public abstract class ActionWithWatchResult implements ActionWithResult<WatchResult> {
    @Override
    public WatchResult negativeResult() {
        return WatchResult.fail("session expired or server error during watch request");
    }
}
