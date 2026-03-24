package mage.client.bridge.processor;

import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

public final class BridgeCursorState {
    private long nextBoardCursorId = 1;
    private final Map<String, Long> boardCursorIds = new HashMap<>();

    public long updateGameStateSnapshotId(String signature) {
        long hash = 0xcbf29ce484222325L;
        byte[] bytes = signature.getBytes(StandardCharsets.UTF_8);
        for (byte b : bytes) {
            hash ^= (b & 0xff);
            hash *= 0x100000001b3L;
        }
        // Keep the snapshot id deterministic across replays without exposing a huge 64-bit value.
        long snapshotId = hash & 0xffffffffffL;
        if (snapshotId == 0) {
            return 1L;
        }
        return snapshotId;
    }

    public long updateBoardCursor(String signature) {
        Long existing = boardCursorIds.get(signature);
        if (existing != null) {
            return existing;
        }
        long assigned = nextBoardCursorId++;
        boardCursorIds.put(signature, assigned);
        return assigned;
    }

    public void reset() {
        nextBoardCursorId = 1;
        boardCursorIds.clear();
    }
}
