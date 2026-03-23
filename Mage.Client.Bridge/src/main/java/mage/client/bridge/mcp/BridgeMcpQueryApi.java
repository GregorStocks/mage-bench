package mage.client.bridge.mcp;

import mage.client.bridge.processor.BridgeCommand;
import mage.client.bridge.processor.BridgeGameLogRefresher;
import mage.client.bridge.processor.BridgePublishedGameState;
import mage.client.bridge.processor.BridgePublishedLogEntry;
import mage.client.bridge.processor.BridgePublishedQuerySnapshot;
import mage.client.bridge.processor.BridgePublishedQueryState;
import mage.client.bridge.processor.BridgeProcessor;
import mage.client.bridge.processor.BridgeQueryCommandService;
import mage.client.bridge.tools.ActionResult;
import mage.client.bridge.tools.GetGameHistoryTool;
import mage.client.bridge.tools.GetGameLogTool;
import mage.client.bridge.tools.GetGameStateTool;
import mage.client.bridge.tools.GetOracleTextTool;
import mage.game.BridgeLogEntry;
import org.apache.log4j.Logger;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

public final class BridgeMcpQueryApi {
    private final String username;
    private final Logger logger;
    private final BridgeProcessor processor;
    private final BridgeGameLogRefresher gameLogRefresher;
    private final BridgePublishedQueryState publishedQueryState;
    private final BridgeQueryCommandService queryCommandService;

    public BridgeMcpQueryApi(
            String username,
            Logger logger,
            BridgeProcessor processor,
            BridgeGameLogRefresher gameLogRefresher,
            BridgePublishedQueryState publishedQueryState,
            BridgeQueryCommandService queryCommandService) {
        this.username = username;
        this.logger = logger;
        this.processor = processor;
        this.gameLogRefresher = gameLogRefresher;
        this.publishedQueryState = publishedQueryState;
        this.queryCommandService = queryCommandService;
    }

    public boolean isActionPending() {
        return snapshotForRead().actionChoices().actionPending();
    }

    public ActionResult getActionChoices(Long boardCursorParam) {
        return snapshotForRead().actionChoices().copyForRead(boardCursorParam);
    }

    public ActionResult getActionChoicesSafe(Long boardCursorParam) {
        return getActionChoices(boardCursorParam);
    }

    public GetGameLogTool.Result getGameLogChunk(int maxChars, Integer cursor) {
        BridgeGameLogSnapshot snapshot = snapshotGameLog();
        List<BridgePublishedLogEntry> allEntries = snapshot.entries();

        if (cursor != null) {
            int effectiveCursor = normalizeSnapshotCursor(snapshot, cursor);
            List<BridgePublishedLogEntry> responseEntries = allEntries.stream()
                .filter(entry -> entry.seq() >= effectiveCursor)
                .toList();

            Map<String, Integer> priorTurns = new HashMap<>();
            for (BridgePublishedLogEntry entry : allEntries) {
                if (entry.seq() >= effectiveCursor) {
                    break;
                }
                if (!entry.isBridgeEvent()) {
                    continue;
                }
                BridgeLogEntry event = entry.bridgeEvent();
                if ("BEGIN_TURN".equals(event.type())) {
                    priorTurns.merge(event.activePlayer(), 1, Integer::sum);
                }
            }

            String rendered = renderGameLogFlat(responseEntries, priorTurns);
            GetGameLogTool.Result result = buildGameLogResult(snapshot, rendered, null, maxChars);
            if (effectiveCursor != cursor) {
                result.cursor_reset = true;
            }
            return result;
        }

        String rendered = renderGameLogFlat(allEntries, Map.of());
        return buildGameLogResult(snapshot, rendered, rendered.length(), maxChars);
    }

    public GetGameLogTool.Result getGameLogSinceTurn(String player, int sinceTurn) {
        String effectivePlayer = player != null ? player : username;
        BridgeGameLogSnapshot snapshot = snapshotGameLog();
        List<BridgePublishedLogEntry> allEntries = snapshot.entries();

        String allRendered = renderGameLogFlat(allEntries, Map.of());

        Map<String, Integer> priorTurns = new HashMap<>();
        int startCursor = -1;
        for (BridgePublishedLogEntry entry : allEntries) {
            if (!entry.isBridgeEvent()) {
                continue;
            }
            BridgeLogEntry event = entry.bridgeEvent();
            if ("BEGIN_TURN".equals(event.type())) {
                int count = priorTurns.merge(event.activePlayer(), 1, Integer::sum);
                if (effectivePlayer.equals(event.activePlayer()) && count == sinceTurn) {
                    priorTurns.merge(effectivePlayer, -1, Integer::sum);
                    startCursor = entry.seq();
                    break;
                }
            }
        }

        if (startCursor >= 0) {
            int effectiveCursor = startCursor;
            List<BridgePublishedLogEntry> subset = allEntries.stream()
                .filter(entry -> entry.seq() >= effectiveCursor)
                .toList();
            GetGameLogTool.Result result = buildGameLogResult(
                    snapshot,
                    renderGameLogFlat(subset, priorTurns),
                    allRendered.length(),
                    null
            );
            result.truncated = false;
            result.since_turn = sinceTurn;
            result.since_player = effectivePlayer;
            return result;
        }

        int totalPlayerTurns = priorTurns.getOrDefault(effectivePlayer, 0);
        if (totalPlayerTurns > 0 && sinceTurn <= totalPlayerTurns) {
            GetGameLogTool.Result result = buildGameLogResult(snapshot, allRendered, allRendered.length(), null);
            result.truncated = true;
            result.since_player = effectivePlayer;
            return result;
        }

        GetGameLogTool.Result result = buildGameLogResult(snapshot, "", allRendered.length(), null);
        result.truncated = false;
        return result;
    }

    public GetGameHistoryTool.Result getGameHistory(Integer sinceTurn, Integer sinceCursor) {
        BridgeGameLogSnapshot snapshot = snapshotGameLog();
        int effectiveCursor = sinceCursor != null
            ? normalizeHistoryCursor(snapshot, sinceCursor)
            : snapshot.firstCursor();
        List<BridgeLogEntry> events = snapshot.entries().stream()
            .filter(BridgePublishedLogEntry::isBridgeEvent)
            .filter(entry -> entry.seq() >= effectiveCursor)
            .map(BridgePublishedLogEntry::bridgeEvent)
            .toList();

        if (sinceTurn != null) {
            events = events.stream()
                    .filter(e -> e.turn() >= sinceTurn)
                    .toList();
        }
        return BridgeGameLogFormatter.buildGameHistoryResult(events, snapshot.nextCursor());
    }

    public GetGameStateTool.Result getGameState(Long snapshotId) {
        return buildGameStateFromPublished(snapshotId, snapshotGameStateForRead());
    }

    public GetGameStateTool.Result getGameState() {
        return getGameState(null);
    }

    public Map<String, Object> getMyDecklist() {
        return processor.submit(BridgeCommand.of(queryCommandService::getMyDecklist));
    }

    public GetOracleTextTool.Result getOracleText(
            String cardName,
            String objectId,
            String[] cardNames,
            String[] objectIds) {
        return processor.submit(BridgeCommand.of(
            () -> queryCommandService.getOracleText(cardName, objectId, cardNames, objectIds)
        ));
    }

    private BridgeGameLogSnapshot snapshotGameLog() {
        long syncEpoch = processor.submit(BridgeCommand.of(gameLogRefresher::captureSyncBarrierEpoch));
        gameLogRefresher.awaitSyncThrough(syncEpoch);
        return processor.submit(BridgeCommand.of(() -> {
            var gameLog = publishedQueryState.snapshot().gameLog();
            return new BridgeGameLogSnapshot(gameLog.entries(), gameLog.firstCursor(), gameLog.nextCursor());
        }));
    }

    private BridgePublishedGameState snapshotGameStateForRead() {
        return snapshotForRead().gameState();
    }

    private String renderGameLogFlat(
            List<BridgePublishedLogEntry> entries,
            Map<String, Integer> initialTurnCounts) {
        return BridgeGameLogFormatter.renderGameLogFlat(entries, initialTurnCounts);
    }

    private GetGameStateTool.Result buildGameStateFromPublished(Long snapshotId, BridgePublishedGameState snapshot) {
        if (!snapshot.available()) {
            var unavailable = new GetGameStateTool.Result();
            unavailable.available = false;
            unavailable.error = snapshot.error();
            return unavailable;
        }
        if (snapshotId != null && snapshotId.longValue() == snapshot.snapshotId().longValue()) {
            var unchanged = new GetGameStateTool.Result();
            unchanged.available = true;
            unchanged.unchanged = true;
            unchanged.snapshot_id = snapshot.snapshotId();
            return unchanged;
        }
        var state = new GetGameStateTool.Result();
        state.available = true;
        state.snapshot_id = snapshot.snapshotId();
        state.game_seq = snapshot.gameSeq();
        String step = snapshot.step() != null ? snapshot.step() : "null";
        logger.debug("[" + username + "] getGameState returning game_seq="
                + snapshot.gameSeq() + " step=" + step
                + " thread=" + Thread.currentThread().getName());
        state.turn = snapshot.turn();
        state.phase = snapshot.phase();
        state.step = snapshot.step();
        state.active_player = snapshot.activePlayer();
        state.priority_player = snapshot.priorityPlayer();
        state.players = snapshot.players();
        state.stack = snapshot.stack();
        state.combat = snapshot.combat();
        return state;
    }

    private BridgePublishedQuerySnapshot snapshotForRead() {
        return processor.submit(BridgeCommand.of(publishedQueryState::snapshot));
    }

    private GetGameLogTool.Result buildGameLogResult(
            BridgeGameLogSnapshot snapshot,
            String rendered,
            Integer totalLength,
            Integer maxChars) {
        return BridgeGameLogFormatter.buildGameLogResult(snapshot.nextCursor(), rendered, totalLength, maxChars);
    }

    private int normalizeSnapshotCursor(BridgeGameLogSnapshot snapshot, int requestedCursor) {
        if (requestedCursor < snapshot.firstCursor() || requestedCursor > snapshot.nextCursor()) {
            return snapshot.firstCursor();
        }
        return requestedCursor;
    }

    private int normalizeHistoryCursor(BridgeGameLogSnapshot snapshot, int requestedCursor) {
        if (requestedCursor < snapshot.firstCursor()) {
            return snapshot.firstCursor();
        }
        if (requestedCursor > snapshot.nextCursor()) {
            return snapshot.nextCursor();
        }
        return requestedCursor;
    }
}
