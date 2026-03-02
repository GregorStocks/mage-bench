package mage.client.bridge.tools;

import java.util.List;
import java.util.Map;

import mage.client.bridge.BridgeCallbackHandler;

import static mage.client.bridge.tools.McpToolRegistry.example;
import static mage.client.bridge.tools.McpToolRegistry.json;

public class ChooseActionTool {
    @Tool(
        name = "choose_action",
        description = "Respond to pending action. Blocks until an action is pending. "
            + "Use choice for ID/index/yes/no, attackers/blockers for batch combat.",
        output = {
            @Tool.Field(name = "success", type = "boolean", description = "Whether accepted"),
            @Tool.Field(name = "action_taken", type = "string", description = "What was done, e.g. selected_0, yes, batch_attack"),
            @Tool.Field(name = "error", type = "string", description = "Error message"),
            @Tool.Field(name = "error_code", type = "string",
                description = "no_pending_action, missing_param, index_out_of_range, invalid_choice, internal_error, unknown_action_type"),
            @Tool.Field(name = "retryable", type = "boolean",
                description = "Can retry with different parameters"),
            @Tool.Field(name = "warning", type = "string", description = "Warning message"),
            @Tool.Field(name = "mana_plan_set", type = "boolean", description = "Mana plan was stored"),
            @Tool.Field(name = "mana_plan_size", type = "integer", description = "Entries in stored mana plan"),
            @Tool.Field(name = "declared", type = "array", description = "Declared attacker/blocker IDs"),
            @Tool.Field(name = "failed", type = "array", description = "Failed batch entries: {id, reason}"),
            @Tool.Field(name = "interrupted", type = "boolean", description = "Batch combat interrupted by trigger"),
            @Tool.Field(name = "game_seq", type = "integer", description = "Sequence number"),
            @Tool.Field(name = "next_action_pending", type = "boolean",
                description = "Follow-up action arrived (call get_action_choices/choose_action, not pass_priority)"),
            @Tool.Field(name = "next_action_type", type = "string",
                description = "Follow-up action callback name"),
            @Tool.Field(name = "next_action_message", type = "string",
                description = "Follow-up action message"),
            @Tool.Field(name = "next_action_hint", type = "string",
                description = "How to handle the follow-up action"),
            @Tool.Field(name = "choices", type = "array[object]",
                description = "Choices (on errors, for self-correction)"),
            @Tool.Field(name = "player_dead", type = "boolean", description = "Whether you died"),
            @Tool.Field(name = "game_over", type = "boolean", description = "Whether the game ended"),
            @Tool.Field(name = "recent_chat", type = "array[string]", description = "New chat messages")
        }
    )
    public static Map<String, Object> execute(
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
