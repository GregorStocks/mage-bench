package mage.client.bridge.processor;

import java.nio.charset.StandardCharsets;

public final class BridgeCursorState {
    private long boardCursor = 0;
    private String lastBoardSignature = null;

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
        if (lastBoardSignature == null || !lastBoardSignature.equals(signature)) {
            boardCursor++;
            lastBoardSignature = signature;
        }
        return boardCursor;
    }

    public void reset() {
        boardCursor = 0;
        lastBoardSignature = null;
    }
}
