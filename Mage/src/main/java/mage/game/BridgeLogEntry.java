package mage.game;

import java.io.Serializable;

/**
 * A structured event entry for the bridge event log.
 * <p>
 * Captured server-side from GameEvent firings in {@link GameImpl#fireEvent},
 * buffered in-memory, and pulled by the bridge client via the RPC chain.
 * <p>
 * Human-readable descriptions are generated client-side from these
 * structured fields — the server stores raw data only.
 */
public record BridgeLogEntry(
        int index,           // buffer position, used as pull cursor
        int gameSeq,         // current gameSeq at time of recording, for correlation
        String type,         // GameEvent.EventType name (SPELL_CAST, LAND_PLAYED, etc.)
        int turn,            // turn number
        String phase,        // TurnPhase name (nullable)
        String step,         // PhaseStep name (nullable)
        String activePlayer, // whose turn it is
        String player,       // who performed the action
        String cardName,     // card/permanent involved (nullable, redacted for hidden info)
        String targetName,   // target player/permanent (nullable)
        int amount,          // life/damage amount (0 if N/A)
        boolean visibleToAll // false if this event contains player-private info
) implements Serializable {

    private static final long serialVersionUID = 1L;

    /**
     * Return a copy with cardName redacted (set to null).
     * Used for visibility filtering when delivering to non-owning players.
     */
    public BridgeLogEntry redacted() {
        return new BridgeLogEntry(
                index, gameSeq, type, turn, phase, step,
                activePlayer, player, null, targetName, amount, visibleToAll
        );
    }
}
