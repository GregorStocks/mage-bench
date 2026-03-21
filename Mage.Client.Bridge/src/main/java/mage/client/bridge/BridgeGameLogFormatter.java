package mage.client.bridge;

import mage.client.bridge.tools.GetGameHistoryTool;
import mage.client.bridge.tools.GetGameLogTool;
import mage.game.BridgeLogEntry;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

final class BridgeGameLogFormatter {

    private BridgeGameLogFormatter() {
    }

    static GetGameLogTool.Result buildGameLogResult(
            int cursor,
            String rendered,
            Integer totalLength,
            Integer maxChars
    ) {
        var result = new GetGameLogTool.Result();
        result.cursor = cursor;
        if (totalLength != null) {
            result.total_length = totalLength;
        }
        if (maxChars != null) {
            applyGameLogCharLimit(result, rendered, maxChars);
        } else {
            result.log = rendered;
        }
        return result;
    }

    static GetGameHistoryTool.Result buildGameHistoryResult(List<BridgeLogEntry> events, int cursor) {
        if (events.isEmpty()) {
            var result = new GetGameHistoryTool.Result();
            result.history = "No game events recorded yet.";
            result.cursor = cursor;
            result.event_count = 0;
            return result;
        }

        StringBuilder sb = new StringBuilder();
        int currentTurn = -1;
        String currentPhaseStep = null;

        for (BridgeLogEntry entry : events) {
            if (entry.turn() != currentTurn) {
                currentTurn = entry.turn();
                currentPhaseStep = null;
                if (sb.length() > 0) {
                    sb.append("\n");
                }
                sb.append("Turn ").append(currentTurn);
                if (entry.activePlayer() != null) {
                    sb.append(" (").append(entry.activePlayer()).append(")");
                }
                sb.append(":\n");
            }

            String phaseStep = formatPhaseStep(entry.phase(), entry.step());
            if (phaseStep != null && !phaseStep.equals(currentPhaseStep)) {
                currentPhaseStep = phaseStep;
                sb.append("  ").append(phaseStep).append(":\n");
            }

            String desc = formatBridgeEvent(entry);
            if (desc != null) {
                sb.append("    - ").append(desc).append("\n");
            }
        }

        var result = new GetGameHistoryTool.Result();
        result.history = sb.toString();
        result.cursor = cursor;
        result.event_count = events.size();
        return result;
    }

    static String renderGameLogFlat(
            List<BridgeLogEntry> events,
            List<BridgeChatLogEntry> chatEntries,
            Map<String, Integer> initialTurnCounts,
            int minChatCursor,
            boolean includeChat
    ) {
        StringBuilder sb = new StringBuilder();
        Map<String, Integer> perPlayerTurns = new HashMap<>(initialTurnCounts);
        String lastTurnHeader = null;

        List<BridgeChatLogEntry> chats = List.of();
        int chatIdx = 0;
        if (includeChat) {
            chats = new ArrayList<>(chatEntries);
            chats.sort(Comparator.comparingInt(BridgeChatLogEntry::eventCursor).thenComparing(BridgeChatLogEntry::message));
            while (chatIdx < chats.size() && chats.get(chatIdx).eventCursor() < minChatCursor) {
                chatIdx++;
            }
        }

        boolean seenFirstTurn = !initialTurnCounts.isEmpty();
        for (BridgeLogEntry entry : events) {
            if (!seenFirstTurn) {
                if ("BEGIN_TURN".equals(entry.type())) {
                    seenFirstTurn = true;
                } else {
                    continue;
                }
            }

            while (chatIdx < chats.size() && chats.get(chatIdx).eventCursor() <= entry.index()) {
                if (sb.length() > 0) {
                    sb.append("\n");
                }
                sb.append(chats.get(chatIdx).rendered());
                chatIdx++;
            }

            if ("BEGIN_TURN".equals(entry.type())) {
                String active = entry.activePlayer();
                int playerTurn = perPlayerTurns.merge(active, 1, Integer::sum);
                String header = active + " turn " + playerTurn + ":";
                if (!header.equals(lastTurnHeader)) {
                    if (sb.length() > 0) {
                        sb.append("\n");
                    }
                    sb.append(header);
                    lastTurnHeader = header;
                }
                continue;
            }

            String desc = formatBridgeEvent(entry);
            if (desc != null) {
                if (sb.length() > 0) {
                    sb.append("\n");
                }
                sb.append(desc);
            }
        }

        while (chatIdx < chats.size()) {
            if (sb.length() > 0) {
                sb.append("\n");
            }
            sb.append(chats.get(chatIdx).rendered());
            chatIdx++;
        }

        return sb.toString();
    }

    static String formatPhaseStep(String phase, String step) {
        if (phase == null && step == null) {
            return null;
        }
        if (step != null) {
            return switch (step) {
                case "UPKEEP" -> "Upkeep";
                case "DRAW" -> "Draw";
                case "PRECOMBAT_MAIN" -> "Precombat Main";
                case "BEGIN_COMBAT" -> "Begin Combat";
                case "DECLARE_ATTACKERS" -> "Declare Attackers";
                case "DECLARE_BLOCKERS" -> "Declare Blockers";
                case "FIRST_COMBAT_DAMAGE", "COMBAT_DAMAGE" -> "Combat Damage";
                case "END_COMBAT" -> "End Combat";
                case "POSTCOMBAT_MAIN" -> "Postcombat Main";
                case "END_TURN" -> "End Step";
                case "CLEANUP" -> "Cleanup";
                default -> step.replace('_', ' ').toLowerCase();
            };
        }
        return phase.replace('_', ' ').toLowerCase();
    }

    static String formatBridgeEvent(BridgeLogEntry entry) {
        String player = entry.player();
        String card = entry.cardName();
        String target = entry.targetName();
        int amount = entry.amount();

        return switch (entry.type()) {
            case "SPELL_CAST" -> player + " cast " + (card != null ? card : "a spell")
                    + (target != null ? " targeting " + target : "");
            case "LAND_PLAYED" -> player + " played " + (card != null ? card : "a land");
            case "ACTIVATED_ABILITY" -> player + " activated "
                    + (card != null ? card + "'s ability" : "an ability")
                    + (target != null ? " targeting " + target : "");
            case "ATTACKER_DECLARED" -> player + " attacked with " + (card != null ? card : "a creature")
                    + (target != null ? " (attacking " + target + ")" : "");
            case "BLOCKER_DECLARED" -> player + " blocked"
                    + (target != null ? " " + target : "")
                    + (card != null ? " with " + card : "");
            case "DESTROYED_PERMANENT" -> (card != null ? card : "A permanent") + " was destroyed"
                    + (player != null ? " (" + player + ")" : "");
            case "SACRIFICED_PERMANENT" -> player + " sacrificed " + (card != null ? card : "a permanent");
            case "COUNTERED" -> (card != null ? card : "A spell") + " was countered"
                    + (target != null ? " (targeting " + target + ")" : "");
            case "GAINED_LIFE" -> player + " gained " + amount + " life";
            case "LOST_LIFE" -> player + " lost " + amount + " life";
            case "DREW_CARD" -> player + " drew" + (card != null ? " " + card : " a card");
            case "BEGIN_TURN" -> null;
            default -> entry.type() + (player != null ? " by " + player : "")
                    + (card != null ? " (" + card + ")" : "");
        };
    }

    private static void applyGameLogCharLimit(GetGameLogTool.Result result, String rendered, int maxChars) {
        if (maxChars > 0 && rendered.length() > maxChars) {
            result.log = truncateFromFront(rendered, maxChars);
            result.truncated = true;
        } else {
            result.log = rendered;
            result.truncated = false;
        }
    }

    private static String truncateFromFront(String text, int maxChars) {
        String truncated = text.substring(text.length() - maxChars);
        int nl = truncated.indexOf('\n');
        if (nl >= 0 && nl < truncated.length() - 1) {
            truncated = truncated.substring(nl + 1);
        }
        return truncated;
    }
}
