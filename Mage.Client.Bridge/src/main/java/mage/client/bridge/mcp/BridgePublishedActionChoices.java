package mage.client.bridge.mcp;

import mage.client.bridge.tools.ActionResult;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class BridgePublishedActionChoices {
    private final ActionResult snapshot;

    private BridgePublishedActionChoices(ActionResult snapshot) {
        this.snapshot = snapshot;
    }

    static BridgePublishedActionChoices empty() {
        var result = new ActionResult();
        result.action_pending = false;
        return new BridgePublishedActionChoices(result);
    }

    public static BridgePublishedActionChoices from(ActionResult result) {
        return new BridgePublishedActionChoices(copyActionResult(result, true, null));
    }

    boolean actionPending() {
        return Boolean.TRUE.equals(snapshot.action_pending);
    }

    ActionResult copyForRead(Long boardCursorParam) {
        return copyActionResult(snapshot, false, boardCursorParam);
    }

    private static ActionResult copyActionResult(ActionResult source, boolean freezeCollections, Long boardCursorParam) {
        var copy = new ActionResult();
        for (Field field : ActionResult.class.getFields()) {
            try {
                Object value = field.get(source);
                if (value != null) {
                    value = copyJsonLike(value, freezeCollections);
                }
                field.set(copy, value);
            } catch (IllegalAccessException e) {
                throw new IllegalStateException("Failed to copy ActionResult field " + field.getName(), e);
            }
        }

        if (boardCursorParam != null
                && source.board_cursor != null
                && boardCursorParam.longValue() == source.board_cursor.longValue()) {
            copy.board = null;
            copy.board_unchanged = true;
        }

        return copy;
    }

    private static Object copyJsonLike(Object value, boolean freezeCollections) {
        if (value instanceof Map<?, ?> map) {
            var copied = new LinkedHashMap<String, Object>();
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                copied.put((String) entry.getKey(), copyJsonLike(entry.getValue(), freezeCollections));
            }
            return freezeCollections ? Collections.unmodifiableMap(copied) : copied;
        }
        if (value instanceof List<?> list) {
            var copied = new ArrayList<>(list.size());
            for (Object entry : list) {
                copied.add(copyJsonLike(entry, freezeCollections));
            }
            return freezeCollections ? Collections.unmodifiableList(copied) : copied;
        }
        return value;
    }
}
