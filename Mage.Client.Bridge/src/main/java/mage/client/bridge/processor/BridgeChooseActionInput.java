package mage.client.bridge.processor;

public record BridgeChooseActionInput(
    Integer index,
    String id,
    Boolean answer,
    Integer amount,
    int[] amounts,
    Integer pile,
    String text,
    String[] manaPlan,
    Boolean autoTap,
    String[] attackers,
    String[] blockers
) {
    public BridgeChooseActionInput {
        amounts = amounts != null ? amounts.clone() : null;
        manaPlan = manaPlan != null ? manaPlan.clone() : null;
        attackers = attackers != null ? attackers.clone() : null;
        blockers = blockers != null ? blockers.clone() : null;
    }

    public boolean usesBatchCombat() {
        return (attackers != null && attackers.length > 0)
            || (blockers != null && blockers.length > 0);
    }
}
