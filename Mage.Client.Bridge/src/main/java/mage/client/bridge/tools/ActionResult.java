package mage.client.bridge.tools;

import java.lang.reflect.Field;
import java.util.List;
import java.util.Map;

/**
 * Typed result for action-related tools (get_action_choices, pass_priority).
 * Both tools share these fields — unused fields are null and omitted from JSON.
 *
 * The mergeFrom() method supports the pass_priority → get_action_choices merge
 * where choices are folded into the pass result to save a round-trip.
 */
public class ActionResult {

    @ResultField(description = "Whether an action is pending")
    public Boolean action_pending;

    @ResultField(description = "XMage callback method name")
    public String action_type;

    @ResultField(description = "Prompt from XMage")
    public String message;

    @ResultField(description = "select, boolean, index, amount, pile, or multi_amount",
        conditional = "action_pending")
    public String response_type;

    @ResultField(description = "choose_action parameter(s) to use",
        conditional = "action_pending")
    public String respond_with;

    @ResultField(description = "Turn/phase context, e.g. T3 PRECOMBAT_MAIN (Player1) YOUR_MAIN",
        conditional = "action_pending")
    public String context;

    @ResultField(description = "Board state (players array). Omitted when board_unchanged=true.",
        conditional = "action_pending")
    public List<Map<String, Object>> board;

    @ResultField(description = "Pass back to skip unchanged board.",
        conditional = "action_pending")
    public Long board_cursor;

    @ResultField(description = "Board omitted (cursor matched).",
        conditional = "action_pending")
    public Boolean board_unchanged;

    @ResultField(description = "Available choices with index and name",
        conditional = "action_pending")
    public List<Map<String, Object>> choices;

    @ResultField(description = "Hand cards (during mulligan)",
        conditional = "action_pending")
    public List<Map<String, Object>> your_hand;

    @ResultField(description = "declare_attackers or declare_blockers")
    public String combat_phase;

    @ResultField(description = "Combat groups",
        conditional = "action_pending")
    public List<Map<String, Object>> combat;

    @ResultField(description = "Stack (when non-empty)",
        conditional = "action_pending")
    public List<Map<String, Object>> stack;

    @ResultField(description = "Untapped land count",
        conditional = "action_pending")
    public Integer untapped_lands;

    @ResultField(description = "Sequence number")
    public Integer game_seq;

    @ResultField(description = "Lands played this turn",
        conditional = "action_pending")
    public Integer land_drops_used;

    @ResultField(description = "Pre-declared attackers",
        conditional = "action_pending")
    public List<Map<String, Object>> already_attacking;

    @ResultField(description = "Attackers (during declare_blockers)",
        conditional = "action_pending")
    public List<Map<String, Object>> incoming_attackers;

    @ResultField(description = "Targeting is required",
        conditional = "action_pending")
    public Boolean required;

    @ResultField(description = "Targeting can be cancelled",
        conditional = "action_pending")
    public Boolean can_cancel;

    @ResultField(description = "Informational note",
        conditional = "action_pending")
    public String note;

    @ResultField(description = "Pile 1 contents",
        conditional = "action_pending")
    public List<Map<String, Object>> pile1;

    @ResultField(description = "Pile 2 contents",
        conditional = "action_pending")
    public List<Map<String, Object>> pile2;

    @ResultField(description = "Min allowed value",
        conditional = "action_pending")
    public Integer min;

    @ResultField(description = "Max allowed value",
        conditional = "action_pending")
    public Integer max;

    @ResultField(description = "Total min (multi_amount)",
        conditional = "action_pending")
    public Integer total_min;

    @ResultField(description = "Total max (multi_amount)",
        conditional = "action_pending")
    public Integer total_max;

    @ResultField(description = "Per-item details for multi_amount",
        conditional = "action_pending")
    public List<Map<String, Object>> items;

    @ResultField(description = "Error message")
    public String error;

    @ResultField(description = "Whether you died")
    public Boolean player_dead;

    @ResultField(description = "Whether the game ended")
    public Boolean game_over;

    @ResultField(description = "New chat messages")
    public List<String> recent_chat;

    @ResultField(description = "Why returned: playable_cards, combat, non_priority_action, "
        + "game_over, reached_step, step_not_reached, stack_resolved, end_of_turn, turn_advanced, interrupted")
    public String stop_reason;

    @ResultField(description = "Current step (for reached_step/step_not_reached)")
    public String current_step;

    @ResultField(description = "What was done when an action was auto-resolved")
    public String action_taken;

    @ResultField(description = "Whether you have playable cards")
    public Boolean has_playable_cards;

    @ResultField(description = "Warning message")
    public String warning;

    /**
     * Merge non-null fields from source into this result where this result's field is null.
     * Used by mergeActionChoices to fold get_action_choices data into pass_priority results.
     */
    public void mergeFrom(ActionResult source) {
        for (Field f : ActionResult.class.getFields()) {
            if (!f.isAnnotationPresent(ResultField.class)) continue;
            try {
                if (f.get(this) == null && f.get(source) != null) {
                    f.set(this, f.get(source));
                }
            } catch (IllegalAccessException e) {
                throw new RuntimeException(e);
            }
        }
    }
}
