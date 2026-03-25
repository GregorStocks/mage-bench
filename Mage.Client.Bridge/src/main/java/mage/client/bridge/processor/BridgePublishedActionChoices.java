package mage.client.bridge.processor;

import mage.client.bridge.tools.ActionResult;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class BridgePublishedActionChoices {
    private final ActionResult snapshot;
    private final List<Object> backingChoices;
    private final long generatedAtMs;

    private BridgePublishedActionChoices(ActionResult snapshot, List<Object> backingChoices, long generatedAtMs) {
        this.snapshot = snapshot;
        this.backingChoices = backingChoices;
        this.generatedAtMs = generatedAtMs;
    }

    public static BridgePublishedActionChoices empty() {
        var result = new ActionResult();
        result.action_pending = false;
        return new BridgePublishedActionChoices(result, List.of(), 0);
    }

    public static BridgePublishedActionChoices from(ActionResult result, List<Object> backingChoices) {
        return new BridgePublishedActionChoices(
            copyActionResult(result, true, null),
            List.copyOf(backingChoices),
            System.currentTimeMillis()
        );
    }

    public boolean actionPending() {
        return Boolean.TRUE.equals(snapshot.action_pending);
    }

    public ActionResult copyForRead(Long boardCursorParam) {
        return copyActionResult(snapshot, false, boardCursorParam);
    }

    public List<Object> backingChoices() {
        return backingChoices;
    }

    public long generatedAtMs() {
        return generatedAtMs;
    }

    public String actionType() {
        return snapshot.action_type;
    }

    public String responseType() {
        return snapshot.response_type;
    }

    public int choiceCount() {
        return snapshot.choices != null ? snapshot.choices.size() : -1;
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
