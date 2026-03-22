package mage.client.bridge.processor;

public final class BridgeCursorState {
    private long gameStateCursor = 0;
    private String lastGameStateSignature = null;
    private long boardCursor = 0;
    private String lastBoardSignature = null;

    public long updateGameStateCursor(String signature) {
        if (lastGameStateSignature == null || !lastGameStateSignature.equals(signature)) {
            gameStateCursor++;
            lastGameStateSignature = signature;
        }
        return gameStateCursor;
    }

    public long updateBoardCursor(String signature) {
        if (lastBoardSignature == null || !lastBoardSignature.equals(signature)) {
            boardCursor++;
            lastBoardSignature = signature;
        }
        return boardCursor;
    }

    public void reset() {
        gameStateCursor = 0;
        lastGameStateSignature = null;
        boardCursor = 0;
        lastBoardSignature = null;
    }
}
