package mage.client.bridge.tools;

import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class ChooseActionTool {

    public static class Result {
        @ResultField(description = "Whether accepted")
        public Boolean success;

        @ResultField(description = "What was done, e.g. selected_0, yes, batch_attack")
        public String action_taken;

        @ResultField(description = "Error message")
        public String error;

        @ResultField(description = "no_pending_action, missing_param, index_out_of_range, invalid_choice, internal_error, unknown_action_type")
        public String error_code;

        @ResultField(description = "Can retry with different parameters")
        public Boolean retryable;

        @ResultField(description = "Warning message")
        public String warning;

        @ResultField(description = "Mana plan was stored")
        public Boolean mana_plan_set;

        @ResultField(description = "Entries in stored mana plan")
        public Integer mana_plan_size;

        @ResultField(description = "Declared attacker/blocker IDs")
        public List<Object> declared;

        @ResultField(description = "Failed batch entries: {id, reason}")
        public List<Object> failed;

        @ResultField(description = "Batch combat interrupted by trigger")
        public Boolean interrupted;

        @ResultField(description = "Sequence number")
        public Integer game_seq;

        @ResultField(description = "Follow-up action arrived (call get_action_choices/choose_action, not pass_priority)")
        public Boolean next_action_pending;

        @ResultField(description = "Follow-up action callback name")
        public String next_action_type;

        @ResultField(description = "Follow-up action message")
        public String next_action_message;

        @ResultField(description = "How to handle the follow-up action")
        public String next_action_hint;

        @ResultField(description = "Choices (on errors, for self-correction)")
        public List<Map<String, Object>> choices;

        @ResultField(description = "Whether you died")
        public Boolean player_dead;

        @ResultField(description = "Whether the game ended")
        public Boolean game_over;

        @ResultField(description = "New chat messages")
        public List<String> recent_chat;
    }

    @Tool(
        name = "choose_action",
        description = "Respond to pending action. Blocks until an action is pending. "
            + "Use choice for ID/index/yes/no, attackers/blockers for batch combat."
    )
    public static Result execute(
            BridgeCallbackHandler handler,
            @Param(description = "ID (\"p3\"), index (\"0\"), or yes/no. "
                + "yes=mulligan/confirm, no=keep/pass.") String choice,
            @Param(description = "Amount value (for amount actions)") Integer amount,
            @Param(description = "Multiple amount values (for multi_amount)") int[] amounts,
            @Param(description = "Pile number: 1 or 2") Integer pile,
            @Param(description = "Text value for GAME_CHOOSE_CHOICE (pick option by name)") String text,
            @Param(description = "Comma-separated permanent IDs to tap for mana (e.g. \"p1,p5:1\"). "
                + ":N selects ability on multi-ability lands. "
                + "Pool colors (RED, BLUE, etc.) only SPEND existing pool mana, not produce. "
                + "Auto-tap fills remaining pips.") String mana_plan,
            @Param(description = "Auto-tap mana (default true). "
                + "Set false with mana_plan to require complete plan.") Boolean auto_tap,
            @Param(description = "Batch attack: comma-separated IDs (e.g. \"p1,p2\") or \"all\". "
                + "Auto-confirms.") String attackers,
            @Param(description = "Batch block: comma-separated \"blocker:attacker\" pairs "
                + "(e.g. \"p5:p1,p6:p2\"). Auto-confirms.") String blockers) {
        // Parse choice into index/id/answer for the handler
        Integer index = null;
        String id = null;
        Boolean answer = null;
        if (choice != null && !choice.isBlank()) {
            String trimmed = choice.trim();
            if (trimmed.equalsIgnoreCase("yes") || trimmed.equalsIgnoreCase("true")) {
                answer = true;
            } else if (trimmed.equalsIgnoreCase("no") || trimmed.equalsIgnoreCase("false")) {
                answer = false;
            } else {
                try {
                    index = Integer.parseInt(trimmed);
                } catch (NumberFormatException e) {
                    id = trimmed;
                }
            }
        }
        // Parse comma-separated strings into arrays
        String[] manaPlanArray = splitCsv(mana_plan);
        String[] attackersArray = splitCsv(attackers);
        String[] blockersArray = splitCsv(blockers);
        // Treat empty arrays/strings as "not provided"
        if (amounts != null && amounts.length == 0) amounts = null;
        if (text != null && text.isEmpty()) text = null;
        return handler.chooseAction(index, id, answer, amount, amounts, pile, text, manaPlanArray, auto_tap, attackersArray, blockersArray);
    }

    private static String[] splitCsv(String value) {
        if (value == null || value.isBlank()) return null;
        String[] parts = value.split(",");
        for (int i = 0; i < parts.length; i++) {
            parts[i] = parts[i].trim();
        }
        return parts;
    }

    public static List<Map<String, Object>> examples() {
        return List.of(
            example("ID-based selection", json(
                "success", true,
                "action_taken", "selected_2")),
            example("Boolean answer", json(
                "success", true,
                "action_taken", "no")),
            example("Cast with mana plan", json(
                "success", true,
                "action_taken", "selected_2",
                "mana_plan_set", true,
                "mana_plan_size", 3)),
            example("Batch attack", json(
                "success", true,
                "action_taken", "batch_attack",
                "declared", List.of("p1", "p2", "p3"))),
            example("Batch block", json(
                "success", true,
                "action_taken", "batch_block",
                "declared", List.of(
                    json("id", "p5", "blocks", "p1"),
                    json("id", "p6", "blocks", "p2")))),
            example("Error", json(
                "success", false,
                "error", "Index 5 is out of range (valid: 0-3). Call get_action_choices to see current options.",
                "error_code", "index_out_of_range",
                "retryable", true)));
    }
}
