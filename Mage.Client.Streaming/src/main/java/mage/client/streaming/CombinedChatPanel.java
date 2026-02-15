package mage.client.streaming;

import mage.client.chat.ChatPanelBasic;
import mage.view.ChatMessage.MessageColor;
import mage.view.ChatMessage.MessageType;

import java.util.Date;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Game log panel for streaming mode that filters spammy messages
 * and routes player chat (TALK) messages to a separate panel.
 */
public class CombinedChatPanel extends ChatPanelBasic {

    private ChatPanelBasic playerChatPanel;
    private RoundTracker roundTracker;
    private StreamingGamePanel gamePanel;

    public CombinedChatPanel() {
        super();
        useExtendedView(VIEW_MODE.GAME);
        setChatType(ChatType.GAME);
        disableInput();  // Observers cannot chat
    }

    public void setPlayerChatPanel(ChatPanelBasic panel) {
        this.playerChatPanel = panel;
    }

    public void setRoundTracker(RoundTracker tracker) {
        this.roundTracker = tracker;
    }

    public void setGamePanel(StreamingGamePanel panel) {
        this.gamePanel = panel;
    }

    // Pattern to match "T<number>" at the start of turnInfo (e.g. "T5" or "T5.1")
    private static final Pattern TURN_NUM_PATTERN = Pattern.compile("^T\\d+");
    // Pattern to match "TURN <number>" at the start of a message (e.g. "TURN 13 for Mad AI 2 (40 - 40)")
    private static final Pattern TURN_MSG_PATTERN = Pattern.compile("^TURN \\d+");

    /**
     * Replace the raw turn number in turnInfo (e.g. "T5.1") with the game round ("T2.1").
     */
    private String fixTurnInfo(String turnInfo) {
        if (turnInfo == null || roundTracker == null) {
            return turnInfo;
        }
        Matcher m = TURN_NUM_PATTERN.matcher(turnInfo);
        if (m.find()) {
            return "T" + roundTracker.getGameRound() + turnInfo.substring(m.end());
        }
        return turnInfo;
    }

    /**
     * Replace the raw turn number in "TURN X for Player" messages with the game round.
     */
    private String fixTurnMessage(String message) {
        if (message == null || roundTracker == null) {
            return message;
        }
        Matcher m = TURN_MSG_PATTERN.matcher(message);
        if (m.find()) {
            return "TURN " + roundTracker.getGameRound() + message.substring(m.end());
        }
        return message;
    }

    @Override
    public void receiveMessage(String username, String message, Date time,
            String turnInfo, MessageType messageType, MessageColor color) {
        // Route player chat messages to the separate chat panel
        if (messageType == MessageType.TALK
                || messageType == MessageType.WHISPER_FROM
                || messageType == MessageType.WHISPER_TO) {
            if (playerChatPanel != null) {
                playerChatPanel.receiveMessage(username, message, time, turnInfo, messageType, color);
            }
            if (gamePanel != null && message != null && !message.isEmpty()) {
                gamePanel.logChatEvent("player_chat", message, username);
            }
            return;
        }

        // Log to JSONL (all messages, before spam filtering)
        // Prepend username when present — the server sends status messages
        // like " has started watching" with the username as a separate field
        if (gamePanel != null && message != null && !message.isEmpty()) {
            String logMessage = (username != null && !username.isEmpty())
                    ? username + message : message;
            gamePanel.logChatEvent("game_action", logMessage, null);
        }

        // Game log messages stay here, with spam filtering
        if (shouldFilterMessage(message)) {
            return;
        }
        super.receiveMessage(username, fixTurnMessage(message), time, fixTurnInfo(turnInfo), messageType, color);
    }

    // Patterns for spammy messages to filter out
    private static final Pattern[] FILTER_PATTERNS = {
        // "Player skip attack" - fires every turn when not attacking
        Pattern.compile(" skip attack$"),
        // "Player draws a card" or "Player draws X cards"
        Pattern.compile(" draws (a card|\\d+ cards)$"),
        // "Player puts CardName [id] from hand/stack onto the Battlefield"
        Pattern.compile(" puts .+ from (hand|stack) onto the Battlefield$"),
    };

    private boolean shouldFilterMessage(String message) {
        if (message == null) {
            return false;
        }
        if (message.startsWith("HOTKEYS:")) {
            return true;
        }
        for (Pattern pattern : FILTER_PATTERNS) {
            if (pattern.matcher(message).find()) {
                return true;
            }
        }
        return false;
    }
}
