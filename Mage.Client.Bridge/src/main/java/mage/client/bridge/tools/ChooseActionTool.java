package mage.client.bridge.tools;

import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class ChooseActionTool {

    /**
     * Result extends ActionResult so choose_action returns the same board/choices/hand
     * fields as pass_priority and get_action_choices when a next action is pending.
     * The choose_action-specific fields (success, error_code, mana_plan_*, etc.) are
     * added here; shared fields (game_seq, error, player_dead, etc.) come from ActionResult.
     */
    public static class Result extends ActionResult {
        @ResultField(description = "Whether accepted")
        public Boolean success;

        @ResultField(description = "no_pending_action, missing_param, index_out_of_range, invalid_choice, batch_failed, internal_error, unknown_action_type")
        public String error_code;

        @ResultField(description = "Can retry with different parameters")
        public Boolean retryable;

        @ResultField(description = "Mana plan was stored")
        public Boolean mana_plan_set;

        @ResultField(description = "Entries in stored mana plan")
        public Integer mana_plan_size;

        @ResultField(description = "Declared attacker/blocker IDs")
        public List<Map<String, Object>> declared;

        @ResultField(description = "Failed batch entries: {id, reason}")
        public List<Map<String, Object>> failed;

        @ResultField(description = "Batch combat interrupted by trigger")
        public Boolean interrupted;
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
        int[] effectiveAmounts = (amounts != null && amounts.length == 0) ? null : amounts;
        String effectiveText = (text != null && text.isEmpty()) ? null : text;
        return handler.chooseAction(index, id, answer, amount, effectiveAmounts, pile, effectiveText, manaPlanArray, auto_tap, attackersArray, blockersArray);
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
                "declared", List.of(
                    json("id", "p1"),
                    json("id", "p2"),
                    json("id", "p3")))),
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
